from __future__ import annotations

import codecs
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import attachment_uploads as uploads
from argus.webapi.attachments import resolve_attachment_refs
from argus.webapi.server import create_app

MIB = 1024 * 1024
BASE = "/api/projects/s-upload/attachments"


@pytest.fixture()
def client(tmp_path):
    for sid in ("s-upload", "s-other"):
        life = tmp_path / "projects" / sid
        workspace = tmp_path / "workspaces" / sid
        life.mkdir(parents=True)
        workspace.mkdir(parents=True)
        write_session_meta(tmp_path, SessionMeta(
            id=sid, cwd=str(life), workdir=str(workspace), launch_cwd=str(workspace),
            created=time.time(), last_active=time.time(),
        ))
    with TestClient(create_app(global_root=tmp_path, auth_token="isolated-upload-test")) as client:
        client.headers["Authorization"] = "Bearer isolated-upload-test"
        yield client


def begin(client, name="data.txt", size=6):
    response = client.post(BASE + "/uploads", json={"name": name, "size_bytes": size})
    assert response.status_code == 200, response.text
    return BASE + "/uploads/" + response.json()["attachment_id"]


def append(client, path, content, offset=0):
    return client.put(path, params={"offset": offset}, content=content,
                      headers={"Content-Type": "application/octet-stream"})


def test_limits_auth_and_precise_admission_boundary(client):
    limits = client.get(BASE + "/limits").json()
    assert limits["max_bytes_per_file"] == 100 * MIB
    assert limits["max_total_bytes"] == 500 * MIB
    assert limits["max_count"] == 5
    assert limits["chunk_bytes"] == 4 * MIB
    assert client.post(BASE + "/uploads", json={"name": "a.txt", "size_bytes": 100 * MIB + 1}).status_code == 413
    assert client.post(BASE + "/uploads", json={"name": "../a.txt", "size_bytes": 1}).status_code == 400
    assert client.post(BASE + "/uploads", json={"name": "a.exe", "size_bytes": 1}).status_code == 400
    assert client.post(BASE + "/uploads", json={"name": "a.zip", "size_bytes": 4}).status_code == 200
    client.headers.pop("Authorization")
    assert client.get(BASE + "/limits").status_code == 401
    assert client.post(BASE + "/uploads", json={"name": "a.txt", "size_bytes": 1}).status_code == 401


def test_offsets_lost_ack_restart_complete_and_cross_project(client, tmp_path):
    path = begin(client)
    assert append(client, path, b"abc").json()["offset"] == 3
    assert append(client, path, b"abc").status_code == 409
    assert client.get(path).json()["offset"] == 3
    assert client.post(path + "/complete").status_code == 409
    assert client.get(path.replace("s-upload", "s-other")).status_code == 404
    state = uploads.AttachmentUploads([tmp_path])
    sid, attachment_id = "s-upload", path.rsplit("/", 1)[1]
    assert state.operate(tmp_path, sid, attachment_id, "status")["offset"] == 3
    assert append(client, path, b"def", 3).status_code == 200
    completed = client.post(path + "/complete")
    assert completed.status_code == 200, completed.text
    attachment = completed.json()["attachment"]
    assert client.post(path + "/complete").json()["attachment"] == attachment
    assert client.delete(path).json()["complete"] is True
    assert resolve_attachment_refs(sid, [{"attachment_id": attachment_id}], global_root=tmp_path)[0]["size_bytes"] == 6
    with pytest.raises(ValueError):
        resolve_attachment_refs("s-other", [{"attachment_id": attachment_id}], global_root=tmp_path)
    raw = client.get(f"/api/projects/{sid}/artifact/raw", params={"path": attachment["relative_path"]},
                     headers={"Range": "bytes=1-3"})
    assert raw.status_code == 206
    assert raw.content == b"bcd"
    assert raw.headers["Content-Range"] == "bytes 1-3/6"


def test_actual_five_100_mib_files_and_legacy_reference_compatibility(client, tmp_path):
    chunk = b"a,b\n" * MIB
    refs = []
    for number in range(5):
        path = begin(client, f"large-{number}.csv", 100 * MIB)
        for offset in range(0, 100 * MIB, len(chunk)):
            response = append(client, path, chunk, offset)
            assert response.status_code == 200, response.text
        assert append(client, path, b"x", 100 * MIB).status_code == 413
        result = client.post(path + "/complete")
        assert result.status_code == 200, result.text
        attachment = result.json()["attachment"]
        assert (tmp_path / "workspaces/s-upload" / attachment["relative_path"]).stat().st_size == 100 * MIB
        refs.append({"attachment_id": attachment["attachment_id"]})
    resolved = resolve_attachment_refs("s-upload", refs, global_root=tmp_path)
    assert sum(row["size_bytes"] for row in resolved) == 500 * MIB
    with pytest.raises(ValueError, match="too many"):
        resolve_attachment_refs("s-upload", [*refs, refs[0]], global_root=tmp_path)
    old = client.post(BASE, files={"files": ("old.md", b"# note", "text/markdown")})
    assert old.status_code == 200
    assert resolve_attachment_refs("s-upload", [{"attachment_id": old.json()["attachments"][0]["attachment_id"]}],
                                   global_root=tmp_path)[0]["original_name"] == "old.md"


@pytest.mark.parametrize(("name", "content", "valid"), [
    ("data.txt", b"\xe4\xb8\xad\xe6\x96\x87\n", True),
    ("data.txt", b"\xff", False),
    ("data.txt", b"a\x00b", False),
    ("data.json", codecs.BOM_UTF8 + b'{"a":[1,true,null,"escaped \\" ok"]}', True),
    ("data.json", b'{"a":1} trailing', False),
    ("data.json", b'{"a":[1,]}', False),
    ("data.json", b'[] []', False),
    ("data.pdf", b"%PDF-1.7\n", True),
    ("data.pdf", b"not a pdf", False),
    ("data.png", b"\x89PNG\r\n\x1a\n", True),
])
def test_incremental_content_validation(client, name, content, valid):
    path = begin(client, name, len(content))
    for offset in range(0, len(content), 2):
        assert append(client, path, content[offset:offset + 2], offset).status_code == 200
    result = client.post(path + "/complete")
    assert result.status_code == (200 if valid else 400), result.text
    if not valid:
        assert client.get(path).json()["complete"] is False
        assert client.delete(path).status_code == 200
        assert client.get(path).status_code == 404


def test_json_memory_bounds_are_explicit(client):
    content = b'"' + b"x" * (uploads.JSON_TOKEN_BYTES + 1) + b'"'
    path = begin(client, "large.json", len(content))
    for offset in range(0, len(content), uploads.CHUNK_BYTES):
        assert append(client, path, content[offset:offset + uploads.CHUNK_BYTES], offset).status_code == 200
    response = client.post(path + "/complete")
    assert response.status_code == 400
    assert "4 MiB" in response.text
    assert client.delete(path).status_code == 200
    content = b"[" * 129 + b"0" + b"]" * 129
    path = begin(client, "deep.json", len(content))
    assert append(client, path, content).status_code == 200
    assert "128-level" in client.post(path + "/complete").text


def test_interrupted_validation_never_registers_the_file(client, tmp_path):
    path = begin(client, "interrupted.json", 7)
    assert append(client, path, b'{"a":1}').status_code == 200
    attachment_id = path.rsplit("/", 1)[1]
    manager = uploads.AttachmentUploads([tmp_path])
    with pytest.raises(uploads.UploadError, match="interrupted"):
        manager.operate(tmp_path, "s-upload", attachment_id, "complete", cancelled=lambda: True)
    assert client.get(path).json()["complete"] is False
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404


def test_chunk_body_is_bounded_without_expanding_other_requests(client):
    path = begin(client, size=10 * MIB)
    assert append(client, path, b"x" * (4 * MIB + 1)).status_code == 413
    assert client.get(path).json()["offset"] == 0
    assert client.post("/api/projects/s-upload/message", json={"text": "x" * (256 * 1024)}).status_code == 413
    assert client.put("/api/projects/s-upload/unrelated", content=b"x" * (256 * 1024 + 1)).status_code == 413


def test_pending_quota_cleanup_and_completed_files_survive(client, tmp_path):
    done = begin(client, size=1)
    assert append(client, done, b"x").status_code == 200
    assert client.post(done + "/complete").status_code == 200
    paths = [begin(client, f"{number}.txt", 100 * MIB) for number in range(5)]
    assert client.post(BASE + "/uploads", json={"name": "six.txt", "size_bytes": 1}).status_code == 429
    for path in [done, *paths]:
        filename = tmp_path / "workspaces/s-upload/.argus/attachments/s-upload" / path.rsplit("/", 1)[1] / ".upload.json"
        value = json.loads(filename.read_text())
        value["updated_at"] = time.time() - uploads.IDLE_SECONDS - 1
        filename.write_text(json.dumps(value))
        if path != done:
            os.utime(filename.parent / ".upload.part", (value["updated_at"], value["updated_at"]))
    uploads.AttachmentUploads([tmp_path]).cleanup()
    assert all(client.get(path).status_code == 404 for path in paths)
    assert client.get(done).json()["complete"] is True


def test_disk_and_concurrency_failures_do_not_register_files(client, monkeypatch):
    path = begin(client)
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    assert append(client, path, b"abc").status_code == 507
    assert client.get(path).json()["offset"] == 0
    assert client.post(BASE + "/uploads", json={"name": "a.txt", "size_bytes": 1}).status_code == 507
    monkeypatch.undo()
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: append(client, path, b"abc"), range(2)))
    assert sorted(row.status_code for row in responses) == [200, 409]
    assert client.get(path).json()["offset"] == 3
    slots = client.app.state.attachment_uploads.slots
    slots.acquire()
    slots.acquire()
    try:
        assert append(client, path, b"def", 3).status_code == 429
    finally:
        slots.release()
        slots.release()


@pytest.mark.skipif(os.name == "nt", reason="POSIX link attack")
def test_pending_payload_cannot_follow_symlink(client, tmp_path):
    path = begin(client)
    part = tmp_path / "workspaces/s-upload/.argus/attachments/s-upload" / path.rsplit("/", 1)[1] / ".upload.part"
    target = tmp_path / "not-an-attachment"
    target.write_bytes(b"safe")
    part.unlink()
    part.symlink_to(target)
    assert append(client, path, b"unsafe").status_code >= 400
    assert target.read_bytes() == b"safe"
    assert client.delete(path).status_code == 200
    assert target.read_bytes() == b"safe"
