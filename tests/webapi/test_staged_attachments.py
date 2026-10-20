"""Draft uploads: chosen files upload at once, removal deletes them, sending keeps them."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import staged_attachments
from argus.webapi.attachment_uploads import AttachmentUploads
from argus.webapi.server import create_app

BASE = "/api/projects/s-draft/attachments"


@pytest.fixture()
def client(tmp_path):
    for sid in ("s-draft", "s-other"):
        life = tmp_path / "projects" / sid
        workspace = tmp_path / "workspaces" / sid
        life.mkdir(parents=True)
        workspace.mkdir(parents=True)
        write_session_meta(tmp_path, SessionMeta(
            id=sid, cwd=str(life), workdir=str(workspace), launch_cwd=str(workspace),
            created=time.time(), last_active=time.time(),
        ))
    with TestClient(create_app(global_root=tmp_path, auth_token="isolated-draft-test")) as client:
        client.headers["Authorization"] = "Bearer isolated-draft-test"
        yield client


def _resumable(client, content=b"hello\n", name="notes.txt"):
    begin = client.post(BASE + "/uploads", json={"name": name, "size_bytes": len(content)})
    assert begin.status_code == 200, begin.text
    path = BASE + "/uploads/" + begin.json()["attachment_id"]
    assert client.put(path, params={"offset": 0}, content=content,
                      headers={"Content-Type": "application/octet-stream"}).status_code == 200
    done = client.post(path + "/complete")
    assert done.status_code == 200, done.text
    return path, done.json()["attachment"]


def _small(client, content=b"a,b\n1,2\n", name="table.csv"):
    response = client.post(BASE, files=[("files", (name, content, "text/csv"))])
    assert response.status_code == 200, response.text
    return response.json()["attachments"][0]


def _ledger(tmp_path, sid="s-draft"):
    path = tmp_path / "projects" / sid / staged_attachments.STAGED_FILENAME
    return json.loads(path.read_text()) if path.exists() else {}


def _folder(tmp_path, attachment, sid="s-draft"):
    return tmp_path / "workspaces" / sid / ".argus/attachments" / sid / attachment["attachment_id"]


def test_both_upload_paths_stage_and_removal_deletes_the_file(client, tmp_path):
    _path, resumable = _resumable(client)
    small = _small(client)
    assert set(_ledger(tmp_path)) == {resumable["attachment_id"], small["attachment_id"]}
    assert _folder(tmp_path, small).is_dir()

    response = client.delete(f"{BASE}/{small['attachment_id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"discarded": True, "attachment_id": small["attachment_id"]}
    assert not _folder(tmp_path, small).exists()
    assert set(_ledger(tmp_path)) == {resumable["attachment_id"]}
    assert client.delete(f"{BASE}/{small['attachment_id']}").status_code == 409


def test_a_sent_file_can_no_longer_be_discarded_or_restaged(client, tmp_path, monkeypatch):
    path, attachment = _resumable(client)
    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message",
                        lambda sid, text, **kwargs: {"kind": "chat", "reply": "ok"})
    sent = client.post("/api/projects/s-draft/message",
                       json={"text": "read this", "attachments": [{"attachment_id": attachment["attachment_id"]}]})
    assert sent.status_code == 200, sent.text
    assert _ledger(tmp_path) == {}
    # A retried completion of the same upload must not stage it again.
    assert client.post(path + "/complete").status_code == 200
    assert _ledger(tmp_path) == {}
    assert client.delete(f"{BASE}/{attachment['attachment_id']}").status_code == 409
    assert _folder(tmp_path, attachment).is_dir()


def test_discard_is_scoped_to_the_project_and_validates_ids(client, tmp_path):
    attachment = _small(client)
    assert client.delete(f"/api/projects/s-other/attachments/{attachment['attachment_id']}").status_code == 409
    assert _folder(tmp_path, attachment).is_dir()
    assert client.delete(f"{BASE}/not-an-id").status_code == 400
    client.headers.pop("Authorization")
    assert client.delete(f"{BASE}/{attachment['attachment_id']}").status_code == 401


def test_week_old_unsent_drafts_are_swept_and_other_files_are_kept(client, tmp_path):
    old = _small(client, name="old.csv")
    fresh = _small(client, name="fresh.csv")
    legacy = _small(client, name="legacy.csv")
    staged_attachments.unstage(tmp_path, "s-draft", [legacy["attachment_id"]])
    staged_attachments.stage(tmp_path, "s-draft", [old["attachment_id"]],
                             now=time.time() - staged_attachments.STAGED_SECONDS - 60)

    AttachmentUploads([tmp_path]).cleanup()

    assert not _folder(tmp_path, old).exists()
    assert _folder(tmp_path, fresh).is_dir()
    assert _folder(tmp_path, legacy).is_dir()
    assert set(_ledger(tmp_path)) == {fresh["attachment_id"]}
