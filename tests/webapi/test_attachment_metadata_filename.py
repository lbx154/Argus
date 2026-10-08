"""Uploaded JSON filenames cannot collide with attachment bookkeeping."""

import json
from pathlib import Path
from uuid import UUID

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import attachments
from argus.webapi.attachments import resolve_attachment_refs, upload_attachments


@pytest.mark.parametrize("filename", ["notes.json", "metadata.json", "Metadata.JSON", "_metadata_.json"])
def test_uploaded_metadata_named_file_preserves_payload_and_resolves(
    tmp_path: Path, filename: str,
) -> None:
    root = tmp_path / "state"
    workspace = tmp_path / "研究 工作区"
    workspace.mkdir()
    sid = "s-upload-metadata"
    write_session_meta(root, SessionMeta(id=sid, cwd=str(workspace), workdir=str(workspace)))
    original = b'{"user_content": "preserve this uploaded document"}'

    result = upload_attachments(
        sid, [(filename, "application/json", original)], global_root=root,
    )

    attachment = result["attachments"][0]
    payload_path = workspace / attachment["relative_path"]
    assert payload_path.read_bytes() == original
    assert attachment["original_name"] == filename
    manifest = json.loads((payload_path.parent / "metadata.json").read_text(encoding="utf-8"))
    assert manifest["attachment_id"] == attachment["attachment_id"]
    assert manifest["size_bytes"] == len(original)
    resolved = resolve_attachment_refs(
        sid, [{"attachment_id": attachment["attachment_id"]}], global_root=root,
    )
    assert len(resolved) == 1
    assert resolved[0]["original_name"] == filename
    assert (workspace / resolved[0]["relative_path"]).read_bytes() == original


def test_windows_atomic_attachment_temporary_name_stays_within_max_path(tmp_path, monkeypatch):
    parent = tmp_path / ("p" * (200 - len(str(tmp_path)) - 1))
    parent.mkdir()
    name = "uploaded-Metadata.json"
    identifier = UUID(int=1)
    monkeypatch.setattr(attachments, "uuid4", lambda: identifier)
    old_temporary = parent / f".{name}.tmp-12345-1000000000000000000-{identifier.hex}"
    assert len(str(old_temporary)) > 259
    assert len(str(parent / name)) <= 259
    opened = []
    original_open = attachments.os.open

    def record_open(path, flags):
        opened.append(Path(path))
        return original_open(path, flags)

    monkeypatch.setattr(attachments.os, "open", record_open)
    attachments._windows_write_file_atomic(parent, name, b"preserved research attachment")
    assert len(opened) == 1
    assert len(str(opened[0])) <= 259
    assert (parent / name).read_bytes() == b"preserved research attachment"
    assert list(parent.iterdir()) == [parent / name]


def test_windows_atomic_attachment_collision_preserves_unowned_temporary(tmp_path, monkeypatch):
    identifier = UUID(int=2)
    monkeypatch.setattr(attachments, "uuid4", lambda: identifier)
    temporary = tmp_path / f".argus-{identifier.hex}.tmp"
    temporary.write_bytes(b"another writer's file")
    target = tmp_path / "metadata.json"
    target.write_bytes(b"previous payload")
    with pytest.raises(FileExistsError):
        attachments._windows_write_file_atomic(tmp_path, target.name, b"new payload")
    assert temporary.read_bytes() == b"another writer's file"
    assert target.read_bytes() == b"previous payload"


def test_windows_atomic_attachment_replace_failure_cleans_only_owned_temporary(tmp_path, monkeypatch):
    target = tmp_path / "metadata.json"
    target.write_bytes(b"previous payload")

    def refuse_replace(source, destination):
        raise OSError("replacement failed")

    monkeypatch.setattr(attachments.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="replacement failed"):
        attachments._windows_write_file_atomic(tmp_path, target.name, b"new payload")
    assert target.read_bytes() == b"previous payload"
    assert list(tmp_path.iterdir()) == [target]
