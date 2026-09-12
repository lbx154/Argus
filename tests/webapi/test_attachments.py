from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import argus_skill.webapi.attachments as attachment_store
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi import server
from argus_skill.webapi.attachments import resolve_attachment_refs, upload_attachments
from argus_skill.webapi.routes.manager import (
    _UPLOAD_READ_CHUNK_BYTES,
    _read_uploaded_attachments,
)

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


class _StubUpload:
    def __init__(self, name: str, content_type: str, parts: list[bytes]) -> None:
        self.filename = name
        self.content_type = content_type
        self._parts = list(parts)
        self.read_sizes: list[int] = []
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if not self._parts:
            return b""
        chunk = self._parts[0]
        if size >= 0 and len(chunk) > size:
            self._parts[0] = chunk[size:]
            return chunk[:size]
        self._parts.pop(0)
        return chunk

    async def close(self) -> None:
        self.closed = True


def _upload_limits(*, max_count: int, max_bytes_per_file: int, max_total_bytes: int) -> dict[str, int]:
    return {
        "max_count": max_count,
        "max_bytes_per_file": max_bytes_per_file,
        "max_total_bytes": max_total_bytes,
    }


def _make_project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    workspace = root / "workspace" / sid
    life.mkdir(parents=True)
    workspace.mkdir(parents=True)
    now = time.time()
    write_session_meta(
        root,
        SessionMeta(
            id=sid,
            created=now,
            last_active=now,
            cwd=str(life),
            workdir=str(workspace),
            launch_cwd=str(workspace),
        ),
    )
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": now}) + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return workspace


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _make_project(tmp_path, "s-upload0")
    _make_project(tmp_path, "s-upload1")
    return TestClient(server.create_app(global_root=tmp_path))


def test_read_uploaded_attachments_rejects_count_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        attachment_store,
        "attachment_limits",
        lambda: _upload_limits(max_count=2, max_bytes_per_file=16, max_total_bytes=32),
    )
    uploads = [
        _StubUpload("a.txt", "text/plain", [b"a"]),
        _StubUpload("b.txt", "text/plain", [b"b"]),
        _StubUpload("c.txt", "text/plain", [b"c"]),
    ]

    with pytest.raises(ValueError, match="too many attachments"):
        asyncio.run(_read_uploaded_attachments(uploads))

    assert all(upload.read_sizes == [] for upload in uploads)
    assert all(upload.closed for upload in uploads)


def test_read_uploaded_attachments_rejects_per_file_limit_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        attachment_store,
        "attachment_limits",
        lambda: _upload_limits(max_count=5, max_bytes_per_file=8, max_total_bytes=32),
    )
    upload = _StubUpload("big.pdf", "application/pdf", [b"%PDF-123", b"4"])

    with pytest.raises(ValueError, match="per-file limit"):
        asyncio.run(_read_uploaded_attachments([upload]))

    assert upload.read_sizes == [8, 1]
    assert all(size <= _UPLOAD_READ_CHUNK_BYTES for size in upload.read_sizes)
    assert upload.closed is True


def test_read_uploaded_attachments_rejects_total_limit_in_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        attachment_store,
        "attachment_limits",
        lambda: _upload_limits(max_count=5, max_bytes_per_file=8, max_total_bytes=13),
    )
    first = _StubUpload("first.pdf", "application/pdf", [b"%PDF-AA"])
    second = _StubUpload("second.pdf", "application/pdf", [b"%PDF-B", b"C"])

    with pytest.raises(ValueError, match="total limit"):
        asyncio.run(_read_uploaded_attachments([first, second]))

    assert first.read_sizes == [8, 1]
    assert second.read_sizes == [6, 1]
    assert all(size <= _UPLOAD_READ_CHUNK_BYTES for size in [*first.read_sizes, *second.read_sizes])
    assert first.closed is True
    assert second.closed is True


def test_upload_attachment_writes_to_canonical_workdir(
    client: TestClient,
    tmp_path: Path,
) -> None:
    payload = b"# operator note\n"

    response = client.post(
        "/api/projects/s-upload0/attachments",
        files=[("files", ("notes.md", payload, "text/markdown"))],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["limits"]["max_count"] == 5
    attachment = body["attachments"][0]
    workspace_target = tmp_path / "workspace" / "s-upload0" / attachment["relative_path"]
    state_target = tmp_path / "projects" / "s-upload0" / attachment["relative_path"]
    assert workspace_target.read_bytes() == payload
    assert not state_target.exists()
    assert attachment["mime"] == "text/markdown"
    assert attachment["original_name"] == "notes.md"
    assert attachment["size_bytes"] == len(payload)
    assert "sha256" not in attachment
    assert "integrity" not in attachment
    assert attachment["relative_path"].startswith(".argus/attachments/s-upload0/att-")


def test_upload_attachment_rejects_pathlike_filename(tmp_path: Path) -> None:
    _make_project(tmp_path, "s-upload0")

    with pytest.raises(ValueError, match="path separators"):
        upload_attachments(
            "s-upload0",
            [("../evil.md", "text/markdown", b"hi")],
            global_root=tmp_path,
        )


def test_message_route_resolves_attachment_metadata(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = client.post(
        "/api/projects/s-upload0/attachments",
        files=[("files", ("report.csv", b"a,b\n1,2\n", "text/csv"))],
    )
    attachment_id = upload.json()["attachments"][0]["attachment_id"]
    seen: dict[str, object] = {}

    def fake_message(
        sid,
        text,
        *,
        global_root=None,
        attachments=None,
        cancelled=None,
    ):
        seen["sid"] = sid
        seen["text"] = text
        seen["attachments"] = attachments
        return {"kind": "chat", "reply": "ok"}

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", fake_message)

    response = client.post(
        "/api/projects/s-upload0/message",
        json={
            "text": "Summarize the attached table.",
            "attachments": [{"attachment_id": attachment_id}],
        },
    )

    assert response.status_code == 200
    forwarded = seen["attachments"]
    assert isinstance(forwarded, list) and len(forwarded) == 1
    assert forwarded[0]["attachment_id"] == attachment_id
    assert forwarded[0]["relative_path"].endswith("/report.csv")
    assert forwarded[0]["mime"] == "text/csv"
    assert forwarded[0]["size_bytes"] == len(b"a,b\n1,2\n")
    assert "sha256" not in forwarded[0]
    assert "integrity" not in forwarded[0]


def test_resolve_legacy_attachment_drops_hash_metadata(tmp_path: Path) -> None:
    workspace = _make_project(tmp_path, "s-upload0")
    uploaded = upload_attachments(
        "s-upload0",
        [("notes.md", "text/markdown", b"hello\n")],
        global_root=tmp_path,
    )["attachments"][0]
    metadata_path = (
        workspace
        / Path(uploaded["relative_path"]).parent
        / "metadata.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["sha256"] = "legacy"
    metadata["integrity"] = "legacy"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    resolved = resolve_attachment_refs(
        "s-upload0",
        [{"attachment_id": uploaded["attachment_id"]}],
        global_root=tmp_path,
    )[0]

    assert "sha256" not in resolved
    assert "integrity" not in resolved


def test_message_stream_route_resolves_attachment_metadata(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = client.post(
        "/api/projects/s-upload0/attachments",
        files=[("files", ("figure.png", b"\x89PNG\r\n\x1a\nPNG", "image/png"))],
    )
    attachment_id = upload.json()["attachments"][0]["attachment_id"]
    seen: dict[str, object] = {}

    def fake_message(
        sid,
        text,
        *,
        global_root=None,
        attachments=None,
        on_fragment=None,
        cancelled=None,
    ):
        _ = (global_root, cancelled)
        seen["sid"] = sid
        seen["text"] = text
        seen["attachments"] = attachments
        on_fragment("delta", {"text": "ok", "message_id": "m-1"})
        return {"kind": "chat", "reply": "ok"}

    monkeypatch.setattr("argus_skill.webapi.manager_bridge.manager_message", fake_message)

    response = client.post(
        "/api/projects/s-upload0/message/stream",
        json={
            "text": "Inspect the figure.",
            "attachments": [{"attachment_id": attachment_id}],
        },
    )

    assert response.status_code == 200
    assert "data:" in response.text
    forwarded = seen["attachments"]
    assert isinstance(forwarded, list) and len(forwarded) == 1
    assert forwarded[0]["attachment_id"] == attachment_id
    assert forwarded[0]["mime"] == "image/png"


def test_message_route_rejects_other_sessions_upload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    upload = upload_attachments(
        "s-upload0",
        [("notes.md", "text/markdown", b"hello\n")],
        global_root=tmp_path,
    )
    attachment_id = upload["attachments"][0]["attachment_id"]
    monkeypatch.setattr(
        "argus_skill.webapi.manager_bridge.manager_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    response = client.post(
        "/api/projects/s-upload1/message",
        json={
            "text": "Read the note.",
            "attachments": [{"attachment_id": attachment_id}],
        },
    )

    assert response.status_code == 400
    assert "unknown attachment_id" in response.text


def test_resolve_attachment_refs_rejects_tampered_payload(tmp_path: Path) -> None:
    workspace = _make_project(tmp_path, "s-upload0")
    upload = upload_attachments(
        "s-upload0",
        [("notes.md", "text/markdown", b"first\n")],
        global_root=tmp_path,
    )
    attachment = upload["attachments"][0]
    (workspace / attachment["relative_path"]).write_text("mutated\n", encoding="utf-8")

    with pytest.raises(ValueError, match="(size|hash) mismatch"):
        resolve_attachment_refs(
            "s-upload0",
            [{"attachment_id": attachment["attachment_id"]}],
            global_root=tmp_path,
        )


def test_upload_rejects_symlinked_session_attachment_root(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    workspace = _make_project(tmp_path, "s-upload0")
    outside = tmp_path / "outside-root"
    outside.mkdir()
    session_root = workspace / ".argus" / "attachments"
    session_root.mkdir(parents=True)
    (session_root / "s-upload0").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        upload_attachments(
            "s-upload0",
            [("notes.md", "text/markdown", b"hello\n")],
            global_root=tmp_path,
        )

    assert list(outside.iterdir()) == []


def test_resolve_attachment_refs_rejects_symlinked_attachment_directory(
    tmp_path: Path,
    require_symlink_support,
) -> None:
    workspace = _make_project(tmp_path, "s-upload0")
    upload = upload_attachments(
        "s-upload0",
        [("notes.md", "text/markdown", b"hello\n")],
        global_root=tmp_path,
    )
    attachment = upload["attachments"][0]
    attachment_dir = workspace / ".argus" / "attachments" / "s-upload0" / attachment["attachment_id"]
    replacement = tmp_path / "outside-attachment"
    replacement.mkdir()
    shutil.rmtree(attachment_dir)
    attachment_dir.symlink_to(replacement, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        resolve_attachment_refs(
            "s-upload0",
            [{"attachment_id": attachment["attachment_id"]}],
            global_root=tmp_path,
        )


@pytest.mark.skipif(os.name != "posix", reason="secure attachment traversal is POSIX-only")
def test_upload_cleanup_does_not_follow_symlinked_attachment_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_project(tmp_path, "s-upload0")
    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep me\n", encoding="utf-8")

    class _FixedUuid:
        hex = "abcdef1234567890"

    def sabotage(parent_fd: int, name: str, content: bytes) -> None:
        _ = (parent_fd, content)
        attachment_dir = workspace / ".argus" / "attachments" / "s-upload0" / "att-abcdef123456"
        attachment_dir.rmdir()
        attachment_dir.symlink_to(outside, target_is_directory=True)
        raise RuntimeError(f"forced failure while writing {name}")

    monkeypatch.setattr(attachment_store, "uuid4", lambda: _FixedUuid())
    monkeypatch.setattr(attachment_store, "_write_file_atomic", sabotage)

    with pytest.raises(RuntimeError, match="forced failure"):
        upload_attachments(
            "s-upload0",
            [("notes.md", "text/markdown", b"hello\n")],
            global_root=tmp_path,
        )

    attachment_dir = workspace / ".argus" / "attachments" / "s-upload0" / "att-abcdef123456"
    assert not attachment_dir.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction security test")
def test_upload_rejects_junctioned_session_attachment_root(tmp_path: Path) -> None:
    workspace = _make_project(tmp_path, "s-upload0")
    outside = tmp_path / "outside-root"
    outside.mkdir()
    session_root = workspace / ".argus" / "attachments"
    session_root.mkdir(parents=True)
    junction = session_root / "s-upload0"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        with pytest.raises(ValueError, match="reparse point"):
            upload_attachments(
                "s-upload0",
                [("notes.md", "text/markdown", b"hello\n")],
                global_root=tmp_path,
            )
        assert list(outside.iterdir()) == []
    finally:
        junction.rmdir()
