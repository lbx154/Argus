from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from argus_skill.webapi import server
from argus_skill.webapi.routes.workspace_v2 import _git, _open_confined_file, _workspace_profiles


def test_workspace_v2_profiles_tree_file_literature_and_confinement(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "project"
    research = workspace / "research"
    search = research / "_search"
    search.mkdir(parents=True)
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "secret.py").write_text("no", encoding="utf-8")
    (research / "note.md").write_text("# Real project note\n", encoding="utf-8")
    (search / "paper.html").write_text("<html>primary source</html>", encoding="utf-8")
    (research / "LITERATURE_GROUNDING.json").write_text(
        json.dumps({
            "papers": [
                {
                    "key": "paper-1",
                    "title": "A Real Primary Paper",
                    "authors": ["Ada Researcher"],
                    "year": 2026,
                    "venue": "ICLR",
                    "url": "https://example.org/paper",
                    "abstract": "Evidence-grounded abstract.",
                    "relevance": "Direct comparison.",
                    "raw_response_path": "research/_search/paper.html",
                },
                {
                    "key": "paper-missing",
                    "title": "Metadata Only Paper",
                    "year": 2025,
                    "url": "https://example.org/metadata",
                    "raw_response_path": "research/_search/missing.html",
                },
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_V2_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARGUS_V2_WORKSPACE_PROFILES", json.dumps([{"label": "Fixture", "path": str(workspace)}]))
    client = TestClient(server.create_app(global_root=tmp_path / "argus-state"))
    sid = "s-fixture01"

    profile_response = client.get("/api/v2/workspaces", params={"sid": sid})
    assert profile_response.status_code == 200
    profile = next(row for row in profile_response.json()["profiles"] if row["label"] == "Fixture")
    context = {"sid": sid, "workspace_id": profile["id"]}

    tree = client.get("/api/v2/workspace/tree", params=context)
    assert tree.status_code == 200
    rows = tree.json()["entries"]
    assert any(row["path"] == "research/note.md" for row in rows)
    assert any(row["path"] == ".venv" and row["skipped"] for row in rows)
    assert not any(row["path"] == ".venv/secret.py" for row in rows)

    file_response = client.get("/api/v2/workspace/file", params={**context, "path": "research/note.md"})
    assert file_response.status_code == 200
    assert "Real project note" in file_response.json()["content"]

    literature = client.get("/api/v2/workspace/literature", params=context)
    assert literature.status_code == 200
    papers = {row["title"]: row for row in literature.json()["papers"]}
    assert papers["A Real Primary Paper"]["evidenceStatus"] == "verified_artifact"
    assert papers["A Real Primary Paper"]["evidencePath"] == "research/_search/paper.html"
    assert "evidenceSha256" not in papers["A Real Primary Paper"]
    assert papers["A Real Primary Paper"]["evidenceBytes"] > 0
    assert papers["Metadata Only Paper"]["evidenceStatus"] == "metadata"

    unknown = client.get("/api/v2/workspace/tree", params={"sid": sid, "workspace_id": "ws-not-approved"})
    assert unknown.status_code == 403

    traversal = client.get("/api/v2/workspace/file", params={**context, "path": "../../etc/passwd"})
    assert traversal.status_code == 400

    invalid_review = client.post(
        f"/api/projects/{sid}/reviews/final",
        json={
            "venue": "ICLR",
            "venue_type": "conference",
            "strictness": "strict",
            "manuscript_path": "paper/malware.exe",
            "emphasis": ["Novelty"],
            "scope": "Check novelty",
        },
    )
    assert invalid_review.status_code == 415


def test_workspace_git_disables_repository_fsmonitor(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "-C", str(workspace), "init"], check=True, capture_output=True)
    marker = tmp_path / "fsmonitor-executed"
    helper = tmp_path / "fsmonitor.sh"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    helper.chmod(0o755)
    subprocess.run(["git", "-C", str(workspace), "config", "core.fsmonitor", str(helper)], check=True)
    (workspace / "probe.txt").write_text("untracked\n", encoding="utf-8")

    status = _git(workspace, "status", "--short")

    assert "probe.txt" in status
    assert not marker.exists()


def test_workspace_v2_requires_bearer_token(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "README.md").write_text("authenticated", encoding="utf-8")
    monkeypatch.setenv("ARGUS_V2_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("ARGUS_V2_WORKSPACE_PROFILES", json.dumps([{"label": "Secure", "path": str(workspace)}]))
    client = TestClient(server.create_app(global_root=tmp_path / "state", auth_token="secret"))

    assert client.get("/api/v2/workspaces", params={"sid": "s-secure01"}).status_code == 401
    authenticated = client.get(
        "/api/v2/workspaces",
        params={"sid": "s-secure01"},
        headers={"Authorization": "Bearer secret"},
    )
    assert authenticated.status_code == 200
    profile = next(row for row in authenticated.json()["profiles"] if row["label"] == "Secure")
    file_response = client.get(
        "/api/v2/workspace/file",
        params={"sid": "s-secure01", "workspace_id": profile["id"], "path": "README.md"},
        headers={"Authorization": "Bearer secret"},
    )
    assert file_response.status_code == 200
    assert file_response.json()["content"] == "authenticated"


def test_canonical_project_workspace_needs_no_machine_specific_allowed_root(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "canonical-project"
    workspace.mkdir()
    (workspace / "README.md").write_text("canonical", encoding="utf-8")
    monkeypatch.delenv("ARGUS_V2_ALLOWED_ROOTS", raising=False)
    monkeypatch.delenv("ARGUS_V2_WORKSPACE_PROFILES", raising=False)

    class Context:
        @staticmethod
        def machine_projects(*, limit: int, include_empty: bool) -> list[dict[str, object]]:
            assert limit == 500
            assert include_empty is True
            return [
                {"id": "s-canonical", "display_name": "Canonical", "workdir": str(workspace)},
                {"id": "s-other", "display_name": "Other", "workdir": str(workspace)},
            ]

    profile = _workspace_profiles(Context(), "s-canonical")[0]
    assert profile["canonical"] is True
    assert profile["id"] == "project:s-canonical"
    assert profile["path"] == str(workspace)

    fd, _info = _open_confined_file(workspace.resolve(), "README.md")
    try:
        assert os.read(fd, 64) == b"canonical"
    finally:
        os.close(fd)


def test_final_review_uses_existing_request_id_without_content_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "project"
    manuscript = workspace / "paper" / "main.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("# Manuscript\n", encoding="utf-8")
    state = tmp_path / "state"
    created = server.create_daemon(workdir=str(workspace), global_root=state)
    sid = created["sid"]
    monkeypatch.setattr(
        server,
        "enqueue_task_command",
        lambda *args, **kwargs: {"ok": True},
    )
    client = TestClient(server.create_app(global_root=state))

    response = client.post(
        f"/api/projects/{sid}/reviews/final",
        json={
            "venue": "ICLR",
            "venue_type": "conference",
            "strictness": "standard",
            "manuscript_path": "paper/main.md",
            "emphasis": ["Novelty"],
            "scope": "Review the final manuscript.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    request_id = payload["request_id"]
    assert request_id.startswith("fr-")
    assert "manuscript_sha256" not in payload["manifest"]

    report = workspace / payload["report_path"]
    report.parent.mkdir(exist_ok=True)
    report.write_text("# Final review\n", encoding="utf-8")
    status = client.get(f"/api/projects/{sid}/reviews/final/{request_id}")

    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert "report_sha256" not in status.json()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction security test")
def test_workspace_v2_rejects_junction_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    junction = workspace / "linked"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        with pytest.raises(HTTPException, match="workspace directory unavailable"):
            _open_confined_file(workspace, "linked/secret.md")
    finally:
        junction.rmdir()
