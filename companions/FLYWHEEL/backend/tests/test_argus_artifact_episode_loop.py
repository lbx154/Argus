from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import foundry.argus_artifact_api as artifact_api_module
import foundry.db as db_module
import foundry.services.argus_artifact_ingest as artifact_ingest_module
import pytest
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings
from foundry.db import MIGRATIONS, Database, utc_now
from foundry.integrations.argus_webapi import ArtifactDownload
from foundry.services.argus_artifact_ingest import (
    MAX_ARTIFACT_BYTES,
    normalize_artifact_entry,
)
from foundry.services.flywheel_data import canonical_json, sha256_text


class FakeArgusArtifacts:
    def __init__(self, artifacts: dict[str, tuple[bytes, str, str]]) -> None:
        self._artifacts = artifacts
        self.download_count = 0

    def artifacts(self, sid: str) -> list[dict]:
        assert sid == "argus-project-1"
        return [
            {
                "path": path,
                "exists": True,
                "kind": kind,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_type": media_type,
            }
            for path, (content, media_type, kind) in self._artifacts.items()
        ]

    def download_artifact(
        self, sid: str, path: str, *, max_bytes: int | None = None
    ) -> ArtifactDownload:
        assert sid == "argus-project-1"
        self.download_count += 1
        content, media_type, _ = self._artifacts[path]
        assert max_bytes is not None and max_bytes <= MAX_ARTIFACT_BYTES
        if len(content) > max_bytes:
            raise RuntimeError("test artifact exceeds max_bytes")
        return ArtifactDownload(
            path=path,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            content_type=media_type,
            content=content,
        )


def test_artifact_index_normalization_drops_non_string_metadata() -> None:
    normalized = normalize_artifact_entry(
        {
            "path": "paper/DRAFT.md",
            "kind": "markdown",
            "exists": True,
            "size": 1,
            "modified_at": float("nan"),
            "name": {"unexpected": "shape"},
        }
    )
    assert normalized["modified_at"] is None
    assert normalized["name"] is None
    json.dumps(normalized, allow_nan=False)


@pytest.fixture()
def artifact_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, FakeArgusArtifacts]:
    settings = Settings(
        database_path=tmp_path / "flywheel.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=tmp_path / "missing-seeds",
        cors_origins=("http://localhost:5175",),
        poll_interval_seconds=0,
        auto_seed=False,
    )
    fake = FakeArgusArtifacts(
        {
            "paper/DRAFT.md": (
                b"# Draft\n\nA bounded, falsifiable result.\n",
                "text/markdown; charset=utf-8",
                "markdown",
            ),
        }
    )
    monkeypatch.setattr(artifact_api_module, "_client", lambda request, connection: fake)
    with TestClient(create_app(settings)) as client:
        yield client, fake


def _team(client: TestClient) -> str:
    draft = client.post(
        "/api/team-intakes/extract",
        json={"raw_text": "系统研究团队，2 张 H100，2000 万 tokens，8 周。"},
    )
    assert draft.status_code == 201, draft.text
    confirmed = client.post(
        f"/api/team-intakes/{draft.json()['id']}/confirm",
        json={
            "actor": "lead",
            "name": "Artifact team",
            "profile": draft.json()["extracted"],
            "training_consent": True,
            "license_basis": "team-owned profile",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["team_profile_id"]


def _episode(client: TestClient, *, training_consent: bool = True) -> dict:
    db = client.app.state.db
    now = utc_now()
    venue_id = db.execute(
        "INSERT INTO venues(venue_key,display_name,created_at,updated_at) VALUES(?,?,?,?)",
        (f"TEST-{uuid.uuid4().hex[:8]}", "Test Venue", now, now),
    )
    connection_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO connections(id,name,kind,base_url,created_at,updated_at) "
        "VALUES(?,?,'local','http://127.0.0.1:9999',?,?)",
        (connection_id, "Bound Argus", now, now),
    )
    campaign_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO campaigns(id,venue_id,connection_id,title,objective,argus_project_id,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            campaign_id,
            venue_id,
            connection_id,
            "Artifact campaign",
            "Produce auditable artifacts",
            "argus-project-1",
            now,
            now,
        ),
    )
    response = client.post(
        "/api/episodes",
        json={
            "title": "Artifact-bound research episode",
            "objective": "Preserve the exact Argus evidence and negative outcomes.",
            "team_profile_id": _team(client),
            "venue_id": venue_id,
            "campaign_id": campaign_id,
            "training_consent": training_consent,
            "license_basis": "team-owned research artifacts",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _stage(
    client: TestClient,
    episode_id: str,
    *,
    path: str = "paper/DRAFT.md",
    role: str = "paper",
    key: str | None = None,
) -> dict:
    index = client.get(f"/api/episodes/{episode_id}/argus-artifacts")
    assert index.status_code == 200, index.text
    item = next(row for row in index.json()["items"] if row["path"] == path)
    staged = client.post(
        f"/api/episodes/{episode_id}/argus-artifact-imports",
        json={
            "artifact_path": path,
            "role": role,
            "expected_entry_sha256": item["entry_sha256"],
            "idempotency_key": key or str(uuid.uuid4()),
        },
    )
    assert staged.status_code == 201, staged.text
    return staged.json()


def _confirm(
    client: TestClient,
    staged: dict,
    *,
    training_consent: bool = True,
    manual: bool = False,
    disposition: str = "as_is",
    replacement_text: str | None = None,
):
    return client.post(
        f"/api/argus-artifact-imports/{staged['id']}/confirm",
        json={
            "actor": "artifact-owner",
            "expected_source_sha256": staged["source_sha256"],
            "redaction_confirmed": True,
            "manual_redaction_confirmed": manual,
            "training_consent": training_consent,
            "license_basis": "team-owned Argus output; internal research training permitted",
            "disposition": disposition,
            **(
                {"replacement_text": replacement_text}
                if replacement_text is not None
                else {}
            ),
        },
    )


def test_argus_artifact_stage_confirm_seal_and_dataset_gate(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
) -> None:
    client, _ = artifact_client
    episode = _episode(client)
    staged = _stage(client, episode["id"])
    assert staged["state"] == "draft"
    assert staged["preview"]["available"] is True
    assert staged["sealed_in_head"] is False
    assert client.app.state.db.fetch_all("SELECT * FROM content_objects") == []
    blocked = client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "must not seal a draft artifact"},
    )
    assert blocked.status_code == 409

    confirmed = _confirm(client, staged)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "confirmed"
    assert confirmed.json()["sealed_in_head"] is False
    assert len(confirmed.json()["content_object_sha256"]) == 64

    sealed = client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "freeze confirmed Argus paper"},
    )
    assert sealed.status_code == 201, sealed.text
    revision = client.app.state.db.fetch_one(
        "SELECT * FROM episode_revisions WHERE id=?", (sealed.json()["id"],)
    )
    assert revision is not None
    manifest = json.loads(revision["manifest_json"])
    assert manifest["schema_version"] == "flywheel.episode/2"
    assert manifest["argus_artifact_imports"][0]["role"] == "paper"
    assert manifest["objects"] == [
        {
            "role": "argus_artifact:paper",
            "sha256": confirmed.json()["content_object_sha256"],
        }
    ]
    detail = client.get(
        f"/api/argus-artifact-imports/{staged['id']}"
    ).json()
    assert detail["sealed_in_head"] is True
    assert detail["sealed_revision_id"] == sealed.json()["id"]
    assert client.get(f"/api/episodes/{episode['id']}/verify").json()["valid"] is True
    preview = client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    # Authentic Argus artifacts do not manufacture research lineage.  This
    # legacy Episode has no conditioned candidate Campaign and stays excluded.
    assert preview["counts"] == {"eligible": 0, "excluded": 1}
    assert "verified_training_lineage_not_frozen" in preview["excluded"][0]["reason"]
    assert client.app.state.db.fetch_all("SELECT * FROM dataset_snapshots") == []


@pytest.mark.parametrize(
    "path",
    [
        "C:\\secret.txt",
        "../secret.txt",
        "/etc/passwd",
        "\\\\server\\share\\paper.md",
        "https://example.test/paper.md",
        "paper/../secret.txt",
    ],
)
def test_stage_rejects_local_absolute_url_and_traversal_paths(
    artifact_client: tuple[TestClient, FakeArgusArtifacts], path: str
) -> None:
    client, _ = artifact_client
    episode = _episode(client)
    response = client.post(
        f"/api/episodes/{episode['id']}/argus-artifact-imports",
        json={
            "artifact_path": path,
            "role": "paper",
            "expected_entry_sha256": "0" * 64,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


def test_fresh_index_digest_idempotency_and_declared_size_limit(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
) -> None:
    client, fake = artifact_client
    episode = _episode(client)
    index = client.get(f"/api/episodes/{episode['id']}/argus-artifacts").json()
    item = index["items"][0]
    key = str(uuid.uuid4())
    stale = client.post(
        f"/api/episodes/{episode['id']}/argus-artifact-imports",
        json={
            "artifact_path": item["path"],
            "role": "paper",
            "expected_entry_sha256": "0" * 64,
            "idempotency_key": key,
        },
    )
    assert stale.status_code == 409
    assert fake.download_count == 0
    first = client.post(
        f"/api/episodes/{episode['id']}/argus-artifact-imports",
        json={
            "artifact_path": item["path"],
            "role": "paper",
            "expected_entry_sha256": item["entry_sha256"],
            "idempotency_key": key,
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/api/episodes/{episode['id']}/argus-artifact-imports",
        json={
            "artifact_path": item["path"],
            "role": "paper",
            "expected_entry_sha256": item["entry_sha256"],
            "idempotency_key": key,
        },
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["idempotent"] is True
    assert fake.download_count == 1

    fake._artifacts["huge.bin"] = (
        b"small-test-placeholder",
        "application/octet-stream",
        "binary",
    )
    original_artifacts = fake.artifacts

    def oversized_index(sid: str) -> list[dict]:
        rows = original_artifacts(sid)
        next(row for row in rows if row["path"] == "huge.bin")["size"] = (
            MAX_ARTIFACT_BYTES + 1
        )
        return rows

    fake.artifacts = oversized_index  # type: ignore[method-assign]
    fresh = client.get(f"/api/episodes/{episode['id']}/argus-artifacts").json()
    huge = next(row for row in fresh["items"] if row["path"] == "huge.bin")
    limited = client.post(
        f"/api/episodes/{episode['id']}/argus-artifact-imports",
        json={
            "artifact_path": "huge.bin",
            "role": "experiment_result",
            "expected_entry_sha256": huge["entry_sha256"],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert limited.status_code == 413
    assert fake.download_count == 1


def test_secret_like_remote_metadata_is_rejected_before_staging(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
) -> None:
    client, fake = artifact_client
    episode = _episode(client)
    original_artifacts = fake.artifacts

    def unsafe_index(sid: str) -> list[dict]:
        rows = original_artifacts(sid)
        rows[0]["modified_at"] = "Bearer abcdefghijklmnopqrstuvwxyz"
        return rows

    fake.artifacts = unsafe_index  # type: ignore[method-assign]
    index = client.get(f"/api/episodes/{episode['id']}/argus-artifacts")
    assert index.status_code == 409
    staged = client.post(
        f"/api/episodes/{episode['id']}/argus-artifact-imports",
        json={
            "artifact_path": "paper/DRAFT.md",
            "role": "paper",
            "expected_entry_sha256": "0" * 64,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert staged.status_code == 409
    assert client.app.state.db.fetch_all("SELECT * FROM argus_artifact_imports") == []
    assert fake.download_count == 0


def test_concurrent_stage_enforces_episode_draft_limit_atomically(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake = artifact_client
    fake._artifacts = {
        "paper/a.md": (b"a", "text/markdown", "markdown"),
        "paper/b.md": (b"b", "text/markdown", "markdown"),
    }
    episode = _episode(client)
    index = client.get(f"/api/episodes/{episode['id']}/argus-artifacts").json()
    by_path = {item["path"]: item for item in index["items"]}
    monkeypatch.setattr(artifact_ingest_module, "MAX_DRAFTS_PER_EPISODE", 1)
    original_download = fake.download_artifact
    barrier = threading.Barrier(2)

    def synchronized_download(
        sid: str, path: str, *, max_bytes: int | None = None
    ) -> ArtifactDownload:
        barrier.wait(timeout=5)
        return original_download(sid, path, max_bytes=max_bytes)

    fake.download_artifact = synchronized_download  # type: ignore[method-assign]

    def stage(path: str) -> int:
        response = client.post(
            f"/api/episodes/{episode['id']}/argus-artifact-imports",
            json={
                "artifact_path": path,
                "role": "paper",
                "expected_entry_sha256": by_path[path]["entry_sha256"],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(stage, ("paper/a.md", "paper/b.md")))
    assert sorted(outcomes) == [201, 413]
    imports = client.app.state.db.fetch_all(
        "SELECT id FROM argus_artifact_imports WHERE episode_id=?", (episode["id"],)
    )
    assert len(imports) == 1
    staging_root = (
        client.app.state.settings.data_dir / "staging" / "argus-artifacts"
    )
    assert len(list(staging_root.rglob("*.stage"))) == 1

def test_secret_requires_text_replacement_and_binary_requires_manual_review(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
) -> None:
    client, fake = artifact_client
    fake._artifacts = {
        "logs/trajectory.txt": (
            b"result=ok\napi_key=abcdefghijklmnopqrstuv\n",
            "text/plain; charset=utf-8",
            "text",
        ),
        "paper/final.pdf": (b"%PDF-1.7\nopaque\xff\n%%EOF", "application/pdf", "binary"),
    }
    episode = _episode(client)
    secret = _stage(client, episode["id"], path="logs/trajectory.txt", role="trajectory")
    assert secret["scan_state"] == "requires_redaction"
    rejected = _confirm(client, secret)
    assert rejected.status_code == 409
    replaced = _confirm(
        client,
        secret,
        disposition="replace_text",
        replacement_text="result=ok\ncredential removed\n",
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["content_object_sha256"] != secret["source_sha256"]

    binary = _stage(client, episode["id"], path="paper/final.pdf", role="paper")
    assert binary["manual_redaction_required"] is True
    manual_block = _confirm(client, binary, manual=False)
    assert manual_block.status_code == 409
    manual_ok = _confirm(client, binary, manual=True)
    assert manual_ok.status_code == 200, manual_ok.text
    row = client.app.state.db.fetch_one(
        "SELECT secret_scan_state,manual_redaction_required FROM content_objects WHERE sha256=?",
        (manual_ok.json()["content_object_sha256"],),
    )
    assert row == {
        "secret_scan_state": "not_scannable_binary",
        "manual_redaction_required": 1,
    }


@pytest.mark.parametrize("consent", ["yes", 1])
def test_training_consent_rejects_coerced_truthy_values(
    artifact_client: tuple[TestClient, FakeArgusArtifacts], consent: object
) -> None:
    client, _ = artifact_client
    episode = _episode(client)
    staged = _stage(client, episode["id"])
    response = client.post(
        f"/api/argus-artifact-imports/{staged['id']}/confirm",
        json={
            "actor": "artifact-owner",
            "expected_source_sha256": staged["source_sha256"],
            "redaction_confirmed": True,
            "manual_redaction_confirmed": False,
            "training_consent": consent,
            "license_basis": "team-owned output",
            "disposition": "as_is",
        },
    )
    assert response.status_code == 422
    assert client.app.state.db.fetch_all("SELECT * FROM content_objects") == []
    row = client.app.state.db.fetch_one(
        "SELECT state FROM argus_artifact_imports WHERE id=?", (staged["id"],)
    )
    assert row == {"state": "draft"}


def test_confirm_cas_terminal_immutability_and_training_rights(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
) -> None:
    client, _ = artifact_client
    episode = _episode(client)
    staged = _stage(client, episode["id"])

    def confirm_once(index: int) -> tuple[int, str]:
        response = _confirm(
            client,
            staged,
            training_consent=False,
            disposition="replace_text",
            replacement_text=f"sanitized concurrent candidate {index}\n",
        )
        return response.status_code, response.text

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(confirm_once, range(2)))
    assert sorted(status for status, _ in outcomes) == [200, 409]
    row = client.app.state.db.fetch_one(
        "SELECT * FROM argus_artifact_imports WHERE id=?", (staged["id"],)
    )
    assert row is not None and row["state"] == "confirmed"
    objects = client.app.state.db.fetch_all("SELECT sha256 FROM content_objects")
    assert objects == [{"sha256": row["content_object_sha256"]}]
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        client.app.state.db.execute(
            "UPDATE argus_artifact_imports SET license_basis='rewritten' WHERE id=?",
            (staged["id"],),
        )
    sealed = client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "archive non-training artifact"},
    )
    assert sealed.status_code == 201, sealed.text
    preview = client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    assert preview["counts"]["eligible"] == 0
    assert "artifact_training_rights_missing" in preview["excluded"][0]["reason"]


def test_discard_is_audited_and_does_not_block_seal(
    artifact_client: tuple[TestClient, FakeArgusArtifacts],
) -> None:
    client, _ = artifact_client
    episode = _episode(client)
    staged = _stage(client, episode["id"])
    discarded = client.post(
        f"/api/argus-artifact-imports/{staged['id']}/discard",
        json={"actor": "lead", "reason": "wrong Argus output selected"},
    )
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["state"] == "discarded"
    assert _confirm(client, staged).status_code == 409
    sealed = client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "discarded artifact is not evidence"},
    )
    assert sealed.status_code == 201, sealed.text


def test_v6_to_v7_migration_and_v1_revision_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "upgrade.db")
    monkeypatch.setattr(
        db_module, "MIGRATIONS", tuple(item for item in MIGRATIONS if item[0] <= 6)
    )
    database.migrate()
    assert database.fetch_one("SELECT MAX(version) AS version FROM schema_migrations") == {
        "version": 6
    }
    monkeypatch.setattr(
        db_module, "MIGRATIONS", tuple(item for item in MIGRATIONS if item[0] <= 7)
    )
    database.migrate()
    assert database.fetch_one("SELECT MAX(version) AS version FROM schema_migrations") == {
        "version": 7
    }
    columns = {
        row["name"] for row in database.fetch_all("PRAGMA table_info(argus_artifact_imports)")
    }
    assert {
        "source_entry_sha256",
        "content_object_sha256",
        "manual_redaction_confirmed",
    } <= columns
    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS)

    settings = Settings(
        database_path=tmp_path / "legacy.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=tmp_path / "missing",
        cors_origins=("http://localhost:5175",),
        poll_interval_seconds=0,
        auto_seed=False,
    )
    with TestClient(create_app(settings)) as client:
        episode = _episode(client)
        raw = client.app.state.db.fetch_one(
            "SELECT * FROM research_episodes WHERE id=?", (episode["id"],)
        )
        assert raw is not None
        now = utc_now()
        revision_id = str(uuid.uuid4())
        manifest = {
            "schema_version": "flywheel.episode/1",
            "episode": {"id": episode["id"]},
            "entity_links": [],
            "review_imports": [],
            "objects": [],
            "provenance": {
                "parent_revision_id": None,
                "parent_chain_sha256": None,
                "revision_number": 1,
                "reason": "legacy v1",
                "sealed_by": "legacy",
                "sealed_at": now,
            },
        }
        manifest_json = canonical_json(manifest)
        manifest_sha = sha256_text(manifest_json)
        chain_sha = sha256_text(
            canonical_json(
                {"manifest_sha256": manifest_sha, "parent_chain_sha256": None}
            )
        )
        with client.app.state.db.transaction() as connection:
            connection.execute(
                "INSERT INTO episode_revisions(id,episode_id,revision_number,parent_revision_id,"
                "manifest_json,manifest_sha256,chain_sha256,object_count,reason,sealed_by,sealed_at) "
                "VALUES(?,?,1,NULL,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    episode["id"],
                    manifest_json,
                    manifest_sha,
                    chain_sha,
                    0,
                    "legacy v1",
                    "legacy",
                    now,
                ),
            )
            connection.execute(
                "UPDATE research_episodes SET head_revision_id=? WHERE id=?",
                (revision_id, episode["id"]),
            )
        verified = client.get(f"/api/episodes/{episode['id']}/verify")
        assert verified.status_code == 200
        assert verified.json()["valid"] is True
