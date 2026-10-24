from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from argus.life.failure_experience import FailureExperience, FailureExperienceStore
from argus.life.failure_experience_index import (
    EmbeddingUnavailable,
    FailureExperienceIndex,
    LexicalEmbedding,
    RecallDocument,
)
from argus.life.memory import MemoryBundle
from argus.life.recall_embedding import (
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
                server.request_headers.append(dict(self.headers))
            server.entered.set()
            if server.gate is not None:
                server.gate.wait(2)
            if server.mode == "slow":
                time.sleep(0.3)
            dimensions = value.get("dimensions", 2)
            if server.mode.startswith("copilot"):
                assert set(value) == {"model", "input", "dimensions"}
                assert isinstance(value["input"], list) and len(value["input"]) == 1
                text = value["input"][0]
                assert isinstance(text, str)
            else:
                text = value["input"]
            vector = [0.0] * dimensions
            vector[0 if any(word in text for word in ("database", "cache")) else min(1, dimensions - 1)] = 1.0
            response = {"model": value["model"], "data": [{"index": 0, "embedding": vector}]}
            if server.mode in {"copilot", "missing_model"}:
                response.pop("model")
            elif server.mode == "copilot_wrong_model":
                response["model"] = "different-model"
            elif server.mode == "copilot_null_model":
                response["model"] = None
            elif server.mode == "nan":
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
    server.request_headers = []
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
    return RecallDocument(name, revision, f"{name}:{revision}", name, "")


def experience(name):
    return FailureExperience.new(mission_id=name, title=name, objective=name, status="failed",
                                 factual_outcome="bounded observation", source_refs=["mission:test"], evidence_refs=["review:test"])


@pytest.mark.parametrize("mode", ["copilot_wrong_model", "copilot_null_model"])
def test_copilot_never_accepts_an_explicit_conflicting_model(tmp_path, embedding_server, mode):
    configure(tmp_path, embedding_server, api_format="copilot")
    embedding_server.mode = mode
    with pytest.raises(EmbeddingUnavailable, match="identity"):
        configured_embedder(tmp_path).embed("database")
    with closing(sqlite3.connect(tmp_path / "embedding/cache.sqlite3")) as db:
        assert db.execute("SELECT COUNT(*) FROM cached_vectors").fetchone()[0] == 0


def test_markdown_semantic_pointer_uses_current_file_and_disappears_after_delete(tmp_path, embedding_server):
    from argus.life.knowledge_recall import KnowledgeRoot, MarkdownKnowledgeRecall

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
    # The batch budget leaves room for slow local cache I/O so the request
    # itself reaches the provider; the provider withholds its answer, so only
    # the per-request timeout can end the call.
    configure(tmp_path, embedding_server, timeout_seconds=0.05, batch_timeout_seconds=1, daily_request_budget=1)
    embedding_server.gate = threading.Event()
    started = time.monotonic()
    try:
        with pytest.raises(EmbeddingUnavailable, match="deadline"):
            configured_embedder(tmp_path).embed("database")
        assert time.monotonic() - started < 1.0
    finally:
        embedding_server.gate.set()
    embedding_server.gate = None
    configure(tmp_path, embedding_server, model="next-model", timeout_seconds=1, batch_timeout_seconds=2, daily_request_budget=1)
    with pytest.raises(EmbeddingUnavailable, match="daily"):
        configured_embedder(tmp_path).embed("different query")
    assert len(embedding_server.requests) == 1


@pytest.mark.parametrize("boundary", ["worker_start", "connect"])
def test_cancelled_late_worker_never_sends_http(tmp_path, embedding_server, monkeypatch, boundary):
    import http.client

    from argus.core.run_gateway import run_interrupt_scope

    configure(tmp_path, embedding_server)
    adapter = configured_embedder(tmp_path)
    stopped, entered, release = threading.Event(), threading.Event(), threading.Event()
    workers = []

    def delay():
        workers.append(threading.current_thread())
        entered.set()
        assert release.wait(2)

    if boundary == "worker_start":
        original_run = threading.Thread.run

        def run(thread):
            if thread.name == "argus-embedding-http":
                delay()
            return original_run(thread)

        monkeypatch.setattr(threading.Thread, "run", run)
    else:
        original_connect = http.client.HTTPConnection.connect

        def connect(connection):
            delay()
            return original_connect(connection)

        monkeypatch.setattr(http.client.HTTPConnection, "connect", connect)

    def invoke():
        with run_interrupt_scope(lambda: "operator abort requested" if stopped.is_set() else None):
            return adapter.embed("database")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(invoke)
        assert entered.wait(1)
        stopped.set()
        try:
            with pytest.raises(EmbeddingUnavailable, match="cancelled"):
                pending.result(timeout=1)
            assert not release.is_set()
            # Once this call has been abandoned, even clearing the originating
            # stop event must not let its delayed worker issue a POST.
            stopped.clear()
        finally:
            release.set()
            for worker in workers:
                worker.join(1)
    assert workers and all(not worker.is_alive() for worker in workers)
    assert not embedding_server.requests
    with closing(sqlite3.connect(tmp_path / "embedding/usage.sqlite3")) as db:
        assert db.execute("SELECT SUM(requests),SUM(input_bytes) FROM budget").fetchone() == (1, len("database"))


def _memory_with_two_embedding_channels(tmp_path, embedding_server):
    memory = MemoryBundle.for_cwd(tmp_path, global_root=tmp_path / "global", fingerprint="project")
    entry = memory.failure_experiences.append(experience("database"))
    skill = memory.project_root / "skills/synthetic/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: synthetic\ndescription: database knowledge\n---\n# Database\nCheck cache consistency.\n")
    canonical = memory.failure_experiences.path.read_bytes()
    configure(memory.project_root, embedding_server)
    return memory, entry, canonical


def test_scope_cancel_during_actual_prelude_skips_second_embedding_and_main_model(tmp_path, embedding_server):
    from argus import SkillLoop, SkillLoopConfig
    from argus.adapters.memory_backend import MemoryBackend
    from argus.core.run_gateway import run_interrupt_scope

    memory, entry, canonical = _memory_with_two_embedding_channels(tmp_path, embedding_server)
    stopped = threading.Event()
    embedding_server.gate = threading.Event()
    backend = MemoryBackend()
    loop = SkillLoop(
        skills_dir=tmp_path / "skills", engineer_runner=backend,
        config=SkillLoopConfig(engineer_model="offline", reviewer_model="offline",
            workflow_mode="direct", active_vertical="software", max_rounds=1,
            require_post_task_learning=False, wiki_enabled=False, auto_init_wiki=False),
        prelude_context_provider=lambda: memory.render_prelude(objective="database cache"),
    )

    def invoke():
        with run_interrupt_scope(lambda: "operator abort requested" if stopped.is_set() else None):
            return loop.run("Check database cache consistency", workdir=tmp_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(invoke)
        assert embedding_server.entered.wait(2)
        workers = [t for t in threading.enumerate() if t.name == "argus-embedding-http"]
        stopped.set()
        try:
            assert pending.result(timeout=1).status == "aborted"
            assert not embedding_server.gate.is_set()
            assert len(embedding_server.requests) == 1
            assert backend.history == []
            for worker in workers:
                worker.join(1)
            assert all(not worker.is_alive() for worker in workers)
        finally:
            embedding_server.gate.set()
    assert memory.failure_experiences.path.read_bytes() == canonical
    assert memory.failure_experiences.get(entry.id).state == "active"
    with closing(sqlite3.connect(memory.project_root / "embedding/usage.sqlite3")) as db:
        assert db.execute("SELECT SUM(requests) FROM budget").fetchone()[0] == 1


def test_supervisor_stop_event_interrupts_the_current_memory_provider(tmp_path, embedding_server):
    from types import SimpleNamespace

    from argus.apps._runtime_backends import _Outcome
    from argus.core.file_lock import current_file_lock_wait_budget
    from argus.core.run_gateway import current_run_interrupt_reason
    from argus.life.memory import BacklogItem
    from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
    from argus.life.supervisor._mission_execution_helpers import _MissionRunState
    from argus.manager.supervision import shutdown_supervision

    memory, entry, canonical = _memory_with_two_embedding_channels(tmp_path, embedding_server)
    stopped = threading.Event()
    embedding_server.gate = threading.Event()
    observed = []

    class Runner:
        def execute(self, *, prelude_context_provider=None, **_kwargs):
            observed.append(prelude_context_provider())
            # The temporary stop scope must not leak into an unrelated call.
            assert current_run_interrupt_reason() is None
            assert current_file_lock_wait_budget() is None
            return _Outcome(success=False, status="aborted")

    supervisor = LifeSupervisor(memory=memory, runner=Runner(),
        sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(project_worktree=tmp_path, stop_event=stopped,
                                    runtime_context="ENGINEER_RUNTIME_ONLY"))
    item = BacklogItem.new(title="next", objective="database cache")
    state = _MissionRunState(item)
    state.prelude = supervisor._build_mission_prelude(item)
    state.cost_sink = SimpleNamespace(handle_event=lambda _event: None)
    state.vertical_root = memory.project_root
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(supervisor._invoke_mission_runner, state)
        assert embedding_server.entered.wait(2)
        workers = [t for t in threading.enumerate() if t.name == "argus-embedding-http"]
        stopped.set()
        try:
            pending.result(timeout=1)
            assert state.exc_str is None
            assert not embedding_server.gate.is_set()
            assert len(embedding_server.requests) == 1
            assert len(observed) == 1
            assert "ENGINEER_RUNTIME_ONLY" in observed[0]
            for worker in workers:
                worker.join(1)
            assert all(not worker.is_alive() for worker in workers)
        finally:
            embedding_server.gate.set()
            shutdown_supervision(memory.project_root)
    assert memory.failure_experiences.path.read_bytes() == canonical
    assert memory.failure_experiences.get(entry.id).state == "active"
    with closing(sqlite3.connect(memory.project_root / "embedding/usage.sqlite3")) as db:
        assert db.execute("SELECT SUM(requests) FROM budget").fetchone()[0] == 1


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("failure", ["unknown-cancellation", "mandatory-context", "unrelated"])
def test_mission_memory_prelude_preserves_unclassified_and_mandatory_failures(legacy, failure):
    from types import SimpleNamespace

    from argus.core.file_lock import FileLockCancelled, current_file_lock_wait_budget
    from argus.core.operator_context import OperatorContextUnavailable
    from argus.core.run_gateway import current_run_interrupt_reason, run_interrupt_scope
    from argus.life.memory import BacklogItem
    from argus.life.supervisor._mission_execution_runtime import _mission_memory_prelude

    error = {
        "unknown-cancellation": FileLockCancelled("unattributed cancellation"),
        "mandatory-context": OperatorContextUnavailable("required context cannot be read"),
        "unrelated": ValueError("invalid memory state"),
    }[failure]

    def read_memory():
        raise error

    item = BacklogItem.new(title="next", objective="database cache")

    def render_current(*, objective, mission_id):
        assert (objective, mission_id) == ("database cache", item.id)
        return read_memory()

    memory = SimpleNamespace(render_prelude=read_memory if legacy else render_current)
    reason = None if failure == "unknown-cancellation" else "daemon stop requested"
    with run_interrupt_scope(lambda: reason):
        with pytest.raises(type(error)) as raised:
            _mission_memory_prelude(memory, item)
        assert raised.value is error
        assert current_file_lock_wait_budget() is None
    assert current_run_interrupt_reason() is None
    assert current_file_lock_wait_budget() is None


def test_batch_budget_preserves_successful_work_for_incremental_reindex(tmp_path, embedding_server):
    configure(tmp_path, embedding_server, max_requests_per_batch=1)
    docs = [document("database"), document("filesystem")]
    index = FailureExperienceIndex(tmp_path / "index.sqlite3", embedder=configured_embedder(tmp_path))
    with pytest.raises(EmbeddingUnavailable, match="batch"):
        index.sync(docs)
    assert len(embedding_server.requests) == 1
    index = FailureExperienceIndex(index.path, embedder=configured_embedder(tmp_path))
    index.sync(docs)
    assert len(embedding_server.requests) == 2
    with closing(sqlite3.connect(index.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_credentials_are_redacted_before_http_and_absent_from_derived_state(redact_secrets_on, tmp_path, embedding_server, monkeypatch, caplog):
    secret = "fixture-private-embedding-bearer-never-persist"
    monkeypatch.setenv("ARGUS_TEST_EMBEDDING_SECRET", secret)
    configure(tmp_path, embedding_server, credential_env="ARGUS_TEST_EMBEDDING_SECRET", cache_entries=1)
    index = FailureExperienceIndex(tmp_path / "index.sqlite3", embedder=configured_embedder(tmp_path))
    index.sync([RecallDocument("safe-id", 1, "v1", "database " + secret, "")])
    assert embedding_server.requests[0][2] == "Bearer " + secret
    assert secret not in embedding_server.requests[0][1]["input"]
    assert secret not in repr(index.embedder) and secret not in caplog.text
    assert all(secret.encode() not in path.read_bytes() for path in tmp_path.rglob("*.sqlite3"))
    assert secret not in (tmp_path / "embedding" / "config.json").read_text()
    index.embedder.embed("another document")
    with closing(sqlite3.connect(tmp_path / "embedding" / "cache.sqlite3")) as db:
        assert db.execute("SELECT COUNT(*) FROM cached_vectors").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [
    {"api_key": "must-not-persist"}, {"endpoint": "http://remote.invalid/embeddings"},
    {"endpoint": "https://user:password@example.invalid/embeddings"}, {"endpoint": "https://example.invalid/?key=secret"},
    {"dimensions": True}, {"dimensions": 4097}, {"timeout_seconds": float("nan")},
    {"api_format": "guess"}, {"api_format": ["copilot"]},
])
def test_invalid_config_is_rejected_without_saving_credentials(tmp_path, changes):
    with pytest.raises(EmbeddingConfigError):
        save_embedding_config(tmp_path, changes)
    assert not (tmp_path / "embedding" / "config.json").exists()
    assert load_embedding_config(tmp_path) == RecallEmbeddingConfig()


def test_query_embedding_does_not_hold_a_database_snapshot_across_a_source_change(tmp_path):
    path = tmp_path / "index.sqlite3"
    writer = FailureExperienceIndex(path)
    writer.sync([document("old")])

    class ConcurrentEmbedding(LexicalEmbedding):
        def embed(self, text):
            writer.sync([document("new")])
            return super().embed(text)

    reader = FailureExperienceIndex(path, embedder=ConcurrentEmbedding())
    with pytest.raises(ValueError, match="current source"):
        reader.scores("query", documents=[document("old")])
    assert set(writer.scores("query", documents=[document("new")])) == {"new"}


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
    assert isinstance(configured_embedder(owner), LexicalEmbedding)
    assert not embedding_server.requests
    assert target.read_text() == ('{"enabled": false}' if name == "config.json" else "peer-private-state")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file boundary")
def test_index_alias_and_special_files_are_rejected_before_read_or_rebuild(tmp_path):
    peer = tmp_path / "peer.sqlite3"
    writer = FailureExperienceIndex(peer)
    writer.sync([document("peer-private")])
    original = peer.read_bytes()
    alias = tmp_path / "owner.sqlite3"
    alias.symlink_to(peer)
    index = FailureExperienceIndex(alias)
    with pytest.raises(ValueError, match="aliases"):
        index.scores("private", documents=[document("peer-private")])
    with pytest.raises(ValueError, match="aliases"):
        index.rebuild([])
    assert peer.read_bytes() == original and alias.is_symlink()
    fifo = tmp_path / "fifo.sqlite3"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular"):
        FailureExperienceIndex(fifo).sync([])
