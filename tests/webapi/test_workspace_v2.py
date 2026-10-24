from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from argus.webapi import daemon_lifecycle, server
from argus.webapi.routes.workspace_v2 import _git, _open_confined_file


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


@pytest.mark.parametrize(
    ("payload", "exit_code", "max_bytes", "expected"),
    [
        ("b''", 0, 4, ""),
        ("b'abcd'", 0, 4, "abcd"),
        ("b'abcde'", 0, 4, "abcd\n… output truncated\n"),
        ("b'x' * (256 * 1024)", 0, 4, "xxxx\n… output truncated\n"),
        ("b'\\xff'", 0, 4, "\ufffd"),
        ("b'error'", 1, 8, ""),
    ],
)
def test_workspace_git_portable_pipe_reads_are_bounded_and_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
    exit_code: int,
    max_bytes: int,
    expected: str,
) -> None:
    real_popen = subprocess.Popen
    processes: list[subprocess.Popen[bytes]] = []
    test_thread = threading.current_thread()

    def spawn(_argv, **kwargs):
        if threading.current_thread() is not test_thread:
            # A daemon thread left by an earlier test on this worker may spawn too.
            return real_popen(_argv, **kwargs)
        process = real_popen(
            [
                sys.executable,
                "-c",
                "import sys; "
                f"sys.stdout.buffer.write({payload}); "
                f"sys.exit({exit_code})",
            ],
            **kwargs,
        )
        processes.append(process)
        return process

    def reject_nonblocking_pipe(*_args):
        pytest.fail("Windows-compatible Git reads must not use os.set_blocking")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(os, "set_blocking", reject_nonblocking_pipe, raising=False)

    assert _git(tmp_path, "status", max_bytes=max_bytes) == expected
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert processes[0].stdout is not None
    assert processes[0].stdout.closed


def test_final_review_uses_existing_request_id_without_content_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "project"
    manuscript = workspace / "paper" / "main.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("# Manuscript\n", encoding="utf-8")
    state = tmp_path / "state"
    created = daemon_lifecycle.create_daemon(workdir=str(workspace), global_root=state)
    sid = created["sid"]
    monkeypatch.setattr(
        "argus.webapi.mission_items.enqueue_task_command",
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
