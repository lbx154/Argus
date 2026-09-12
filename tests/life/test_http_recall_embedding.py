from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from argus_skill.life.failure_experience import FailureExperience, FailureExperienceStore
from argus_skill.life.failure_experience_index import (
    EmbeddingUnavailable,
    FailureExperienceIndex,
    LexicalHashEmbedding,
    RecallDocument,
    _normalized,
)
from argus_skill.life.http_embedding import HttpEmbeddingAdapter
from argus_skill.life.memory import MemoryBundle
from argus_skill.life.recall_embedding import (
    EmbeddingConfigError,
    RecallEmbeddingConfig,
    configured_embedder,
    load_embedding_config,
    save_embedding_config,
)


@pytest.fixture
def embedding_server():
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with server.guard:
                server.requests.append((self.path, value, self.headers.get("Authorization")))
            server.entered.set()
            if server.gate is not None:
                server.gate.wait(2)
            if server.mode == "slow":
                time.sleep(0.3)
            dimensions = value.get("dimensions", 2)
            vector = [0.0] * dimensions
            vector[0 if any(word in value["input"] for word in ("database", "cache")) else min(1, dimensions - 1)] = 1.0
            response = {"model": value["model"], "data": [{"index": 0, "embedding": vector}]}
            if server.mode == "nan":
                response["data"][0]["embedding"][0] = float("nan")
            elif server.mode == "wrong_dimensions":
                response["data"][0]["embedding"] = [1.0]
            elif server.mode == "string_number":
                response["data"][0]["embedding"][0] = "1"
            elif server.mode == "zero":
                response["data"][0]["embedding"] = [0.0] * dimensions
            elif server.mode == "wrong_model":
                response["model"] = "wrong-model"
            raw = json.dumps(response).encode()
            if server.mode == "overflow":
                raw = b'{"model":"fixture","data":[{"index":0,"embedding":[1e999,0]}]}'
            elif server.mode == "oversized":
                raw += b" " * 1024
            status = 302 if server.mode == "redirect" else 503 if server.mode == "failure" else 200
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            if status == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except OSError:
                pass

    server = Server(("127.0.0.1", 0), Handler)
    server.requests, server.mode, server.guard = [], "normal", threading.Lock()
    server.gate, server.entered = None, threading.Event()
    server.endpoint = f"http://127.0.0.1:{server.server_port}/v1/embeddings"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


def configure(root, server, **changes):
    return save_embedding_config(root, {
        "enabled": True, "endpoint": server.endpoint, "model": "fixture", "dimensions": 2,
        **changes,
    })


def document(name, *, revision=1):
    return RecallDocument(name, revision, hashlib.sha256(f"{name}:{revision}".encode()).hexdigest(), name, "")


def experience(name):
    return FailureExperience.new(mission_id=name, title=name, objective=name, status="failed",
                                 factual_outcome="bounded observation", source_refs=["mission:test"], evidence_refs=["review:test"])


def test_default_memory_uses_explicit_lexical_hashing_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_EMBEDDING_ENDPOINT", "https://unused.invalid/v1/embeddings")
    monkeypatch.setattr(HttpEmbeddingAdapter, "_post", lambda *_args: pytest.fail("Default recall made a network request"))
    memory = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path / "global", fingerprint="project")
    memory.failure_experiences.append(experience("database"))
    assert isinstance(memory.failure_experiences.index.embedder, LexicalHashEmbedding)
    assert "database" in memory.render_recall_context("database")
    assert "embedding similarity" not in memory.render_recall_context("database")
    assert not (memory.project_root / "embedding" / "cache.sqlite3").exists()


def test_memory_http_recall_tracks_revisions_deletions_and_model_identity(tmp_path, embedding_server):
    memory = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path / "global", fingerprint="project")
    old = memory.failure_experiences.append(experience("database"))
    memory.failure_experiences.append(experience("unrelated"))
    configure(memory.project_root, embedding_server)
    rendered = memory.render_recall_context("cache")
    assert "database" in rendered and "embedding similarity (advisory)" in rendered
    assert len(embedding_server.requests) == 3
    assert all(row[0] == "/v1/embeddings" and row[1]["encoding_format"] == "float" for row in embedding_server.requests)
    memory.render_recall_context("cache")
    assert len(embedding_server.requests) == 3  # Reopened stores use the durable computation cache.
    first = memory.failure_experiences.index.embedder.identifier
    configure(memory.project_root, embedding_server, model="fixture-v2", dimensions=3)
    memory.render_recall_context("cache")
    store = memory.failure_experiences
    assert store.index.embedder.identifier != first and len(embedding_server.requests) == 6
    store.revise(old.id, expected_revision=1, title="filesystem", objective="filesystem", evidence_refs=["review:updated"])
    with closing(sqlite3.connect(store.index.path)) as db:
        row = db.execute("SELECT revision,direct_terms,vector FROM documents WHERE id=?", (old.id,)).fetchone()
        assert row[0] == 2 and "database" not in row[1] and len(json.loads(row[2])) == 3
    before_delete = len(embedding_server.requests)
    store.retract(old.id, expected_revision=2, evidence_refs=["review:retired"], reason="Scope no longer applies")
    assert len(embedding_server.requests) == before_delete
    with closing(sqlite3.connect(store.index.path)) as db:
        assert db.execute("SELECT id FROM documents WHERE id=?", (old.id,)).fetchone() is None
    assert all(hit.experience.id != old.id for hit in memory.failure_experiences.retrieve("cache"))


def test_markdown_semantic_pointer_uses_current_file_and_disappears_after_delete(tmp_path, embedding_server):
    from argus_skill.life.knowledge_recall import KnowledgeRoot, MarkdownKnowledgeRecall

    pages = tmp_path / "pages"
    pages.mkdir()
    page = pages / "notes.md"
    page.write_text("database consistency")
    configure(tmp_path, embedding_server)
    recall = MarkdownKnowledgeRecall(tmp_path / "knowledge-recall.sqlite3", [KnowledgeRoot("Wiki", pages, tmp_path)])
    assert "notes.md" in recall.render_context("cache")
    page.write_text("unrelated filesystem observations")
    assert "notes.md" not in recall.render_context("cache")
    page.unlink()
    assert recall.render_context("cache") == ""
    with closing(sqlite3.connect(recall.index.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["nan", "overflow", "wrong_dimensions", "string_number", "zero", "wrong_model", "oversized", "redirect", "failure"])
def test_invalid_http_vectors_never_enter_index_or_trigger_retry(tmp_path, embedding_server, mode):
    configure(tmp_path, embedding_server, max_response_bytes=256)
    embedding_server.mode = mode
    store = FailureExperienceStore(tmp_path / "failure_experiences.jsonl")
    stored = store.append(experience("database"))
    assert stored.id and len(embedding_server.requests) == 1
    with closing(sqlite3.connect(store.index.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    assert store.get(stored.id) is not None


def test_input_byte_limit_and_missing_credentials_refuse_before_network(tmp_path, embedding_server, monkeypatch):
    configure(tmp_path, embedding_server, max_input_bytes=5)
    with pytest.raises(EmbeddingUnavailable, match="byte limit"):
        configured_embedder(tmp_path).embed("longer than five bytes")
    configure(tmp_path, embedding_server, credential_env="ARGUS_TEST_EMBEDDING_SECRET", max_input_bytes=8192)
    monkeypatch.delenv("ARGUS_TEST_EMBEDDING_SECRET", raising=False)
    with pytest.raises(EmbeddingUnavailable, match="credential"):
        configured_embedder(tmp_path).embed("database")
    assert not embedding_server.requests


def test_timeout_is_bounded_and_failure_consumes_shared_durable_budget(tmp_path, embedding_server):
    configure(tmp_path, embedding_server, timeout_seconds=0.05, batch_timeout_seconds=0.1, daily_request_budget=1)
    embedding_server.mode = "slow"
    started = time.monotonic()
    with pytest.raises(EmbeddingUnavailable):
        configured_embedder(tmp_path).embed("database")
    assert time.monotonic() - started < 0.5
    configure(tmp_path, embedding_server, model="next-model", timeout_seconds=1, batch_timeout_seconds=2, daily_request_budget=1)
    with pytest.raises(EmbeddingUnavailable, match="daily"):
        configured_embedder(tmp_path).embed("different query")
    assert len(embedding_server.requests) == 1


def test_batch_budget_preserves_successful_work_for_incremental_reindex(tmp_path, embedding_server):
    configure(tmp_path, embedding_server, max_requests_per_batch=1)
    docs = [document("database"), document("filesystem")]
    index = FailureExperienceIndex(tmp_path / "index.sqlite3", embedder=configured_embedder(tmp_path))
    with pytest.raises(EmbeddingUnavailable, match="batch"):
        index.sync(docs, "source")
    assert len(embedding_server.requests) == 1
    index = FailureExperienceIndex(index.path, embedder=configured_embedder(tmp_path))
    index.sync(docs, "source")
    assert len(embedding_server.requests) == 2
    with closing(sqlite3.connect(index.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_daily_input_and_parallel_request_budgets_are_atomic(tmp_path, embedding_server):
    configure(tmp_path, embedding_server, daily_request_budget=1, daily_input_bytes_budget=8)
    def invoke(text):
        try:
            configured_embedder(tmp_path).embed(text)
            return True
        except EmbeddingUnavailable:
            return False
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sum(workers.map(invoke, ["database", "mismatch"])) == 1
    assert len(embedding_server.requests) == 1
    other = tmp_path / "other"
    configure(other, embedding_server, daily_input_bytes_budget=2)
    with pytest.raises(EmbeddingUnavailable, match="daily"):
        configured_embedder(other).embed("three")
    assert len(embedding_server.requests) == 1


def test_discarding_the_vector_cache_does_not_reset_spend_budget(tmp_path, embedding_server):
    configure(tmp_path, embedding_server, daily_request_budget=1)
    configured_embedder(tmp_path).embed("database")
    (tmp_path / "embedding" / "cache.sqlite3").unlink()
    with pytest.raises(EmbeddingUnavailable, match="daily"):
        configured_embedder(tmp_path).embed("new input")
    assert len(embedding_server.requests) == 1


def test_credentials_are_redacted_before_http_and_absent_from_derived_state(tmp_path, embedding_server, monkeypatch, caplog):
    secret = "fixture-private-embedding-bearer-never-persist"
    monkeypatch.setenv("ARGUS_TEST_EMBEDDING_SECRET", secret)
    configure(tmp_path, embedding_server, credential_env="ARGUS_TEST_EMBEDDING_SECRET", cache_entries=1)
    index = FailureExperienceIndex(tmp_path / "index.sqlite3", embedder=configured_embedder(tmp_path))
    index.sync([RecallDocument("safe-id", 1, "source-hash", "database " + secret, "")], "source")
    assert embedding_server.requests[0][2] == "Bearer " + secret
    assert secret not in embedding_server.requests[0][1]["input"]
    assert secret not in repr(index.embedder) and secret not in caplog.text
    assert all(secret.encode() not in path.read_bytes() for path in tmp_path.rglob("*.sqlite3"))
    assert secret not in (tmp_path / "embedding" / "config.json").read_text()
    index.embedder.embed("another document")
    with closing(sqlite3.connect(tmp_path / "embedding" / "cache.sqlite3")) as db:
        assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [
    {"api_key": "must-not-persist"}, {"endpoint": "http://remote.invalid/embeddings"},
    {"endpoint": "https://user:password@example.invalid/embeddings"}, {"endpoint": "https://example.invalid/?key=secret"},
    {"dimensions": True}, {"dimensions": 4097}, {"timeout_seconds": float("nan")},
])
def test_invalid_config_is_rejected_without_saving_credentials(tmp_path, changes):
    with pytest.raises(EmbeddingConfigError):
        save_embedding_config(tmp_path, changes)
    assert not (tmp_path / "embedding" / "config.json").exists()
    assert load_embedding_config(tmp_path) == RecallEmbeddingConfig()


def test_index_refuses_encoder_mismatch_and_normalizes_large_finite_values(tmp_path):
    index = FailureExperienceIndex(tmp_path / "index.sqlite3")
    index.sync([document("database")], "source")
    index.embedder = type("Changed", (LexicalHashEmbedding,), {"identifier": "other-encoder"})()
    with pytest.raises(ValueError, match="current source"):
        index.scores("database", source_digest="source")
    vector = _normalized([1e308, 1e308], 2)
    assert all(math.isfinite(value) for value in vector) and sum(value * value for value in vector) == pytest.approx(1)


def test_query_embedding_does_not_hold_a_database_snapshot_across_a_source_change(tmp_path):
    path = tmp_path / "index.sqlite3"
    writer = FailureExperienceIndex(path)
    writer.sync([document("old")], "old-source")

    class ConcurrentEmbedding(LexicalHashEmbedding):
        def embed(self, text):
            writer.sync([document("new")], "new-source")
            return super().embed(text)

    reader = FailureExperienceIndex(path, embedder=ConcurrentEmbedding())
    with pytest.raises(ValueError, match="current source"):
        reader.scores("query", source_digest="old-source")
    assert set(writer.scores("query", source_digest="new-source")) == {"new"}


def test_slow_sync_does_not_overwrite_an_index_updated_during_embedding(tmp_path):
    path = tmp_path / "index.sqlite3"
    writer = FailureExperienceIndex(path)
    writer.sync([document("old")], "old-source")

    class ConcurrentEmbedding(LexicalHashEmbedding):
        def embed(self, text):
            writer.sync([document("newer")], "newer-source")
            return super().embed(text)

    stale_writer = FailureExperienceIndex(path, embedder=ConcurrentEmbedding())
    with pytest.raises(EmbeddingUnavailable, match="changed"):
        stale_writer.sync([document("slow")], "slow-source")
    assert set(writer.scores("query", source_digest="newer-source")) == {"newer"}


def test_http_recall_does_not_block_retirement_or_return_its_old_snapshot(tmp_path, embedding_server):
    path = tmp_path / "failure_experiences.jsonl"
    old = FailureExperienceStore(path).append(experience("database"))
    configure(tmp_path, embedding_server)
    store = FailureExperienceStore(path)
    embedding_server.gate = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(store.retrieve, "cache")
        assert embedding_server.entered.wait(1)
        try:
            retired = workers.submit(FailureExperienceStore(path).retract, old.id, expected_revision=1,
                                     evidence_refs=["review:retired"], reason="No longer applicable")
            assert retired.result(timeout=0.5).state == "retracted"
        finally:
            embedding_server.gate.set()
        assert pending.result(timeout=1) == []


def test_canonical_write_releases_its_lock_before_http_indexing(tmp_path, embedding_server):
    path = tmp_path / "failure_experiences.jsonl"
    configure(tmp_path, embedding_server)
    store, item = FailureExperienceStore(path), experience("database")
    embedding_server.gate = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = workers.submit(store.append, item)
        assert embedding_server.entered.wait(1)
        try:
            visible = workers.submit(FailureExperienceStore(path).get, item.id)
            assert visible.result(timeout=0.5).id == item.id
        finally:
            embedding_server.gate.set()
        assert pending.result(timeout=1).id == item.id


@pytest.mark.parametrize("name", ["config.json", "cache.sqlite3", "usage.sqlite3", "cache.sqlite3-journal"])
def test_embedding_paths_cannot_alias_another_project(tmp_path, embedding_server, name):
    owner, peer = tmp_path / "owner", tmp_path / "peer"
    configure(owner, embedding_server)
    peer.mkdir()
    target = peer / name
    target.write_text('{"enabled": false}' if name == "config.json" else "peer-private-state")
    alias = owner / "embedding" / name
    alias.unlink(missing_ok=True)
    alias.symlink_to(target)
    assert isinstance(configured_embedder(owner), LexicalHashEmbedding)
    assert not embedding_server.requests
    assert target.read_text() == ('{"enabled": false}' if name == "config.json" else "peer-private-state")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file boundary")
def test_index_alias_and_special_files_are_rejected_before_read_or_rebuild(tmp_path):
    peer = tmp_path / "peer.sqlite3"
    writer = FailureExperienceIndex(peer)
    writer.sync([document("peer-private")], "peer")
    original = peer.read_bytes()
    alias = tmp_path / "owner.sqlite3"
    alias.symlink_to(peer)
    index = FailureExperienceIndex(alias)
    with pytest.raises(ValueError, match="aliases"):
        index.scores("private", source_digest="peer")
    with pytest.raises(ValueError, match="aliases"):
        index.rebuild([], "new")
    assert peer.read_bytes() == original and alias.is_symlink()
    fifo = tmp_path / "fifo.sqlite3"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular"):
        FailureExperienceIndex(fifo).sync([], "empty")
