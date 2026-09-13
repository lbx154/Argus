"""Uploaded JSON filenames cannot collide with attachment bookkeeping."""

import json
from pathlib import Path

import pytest

from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.webapi.attachments import resolve_attachment_refs, upload_attachments


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
