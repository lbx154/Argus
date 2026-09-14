"""Git output must not depend on the Windows ANSI code page."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.server import create_app


@pytest.fixture
def git_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    workspace = tmp_path / "中文 workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    sid = "s-git-encoding"
    life = state / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(state, SessionMeta(id=sid, cwd=str(life), workdir=str(workspace)))

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(workspace), *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "中文分支")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "core.hooksPath", os.devnull)
    git("config", "commit.gpgsign", "false")
    (workspace / "note.md").write_text("初始内容\n", encoding="utf-8")
    git("add", "note.md")
    git("commit", "-qm", "fixture")
    return workspace, state, sid, git


@pytest.mark.parametrize("ansi_encoding", ["cp936", "cp1252", "utf-8"])
def test_git_diff_preserves_unicode_with_legacy_default_encoding(
    git_workspace, monkeypatch, ansi_encoding: str,
) -> None:
    workspace, state, sid, git = git_workspace
    (workspace / "note.md").write_text("已暂存的中文修改\n", encoding="utf-8")
    git("add", "note.md")
    (workspace / "note.md").write_text("修改后的中文进展：研究成功。\n", encoding="utf-8")
    # Exercise real Git and real Popen decoding with a Windows default even on
    # UTF-8 CI hosts. Explicit encodings bypass this CPython default selector.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: ansi_encoding)
    with TestClient(create_app(global_root=state)) as client:
        response = client.get(f"/api/projects/{sid}/git-diff")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["branch"] == "中文分支"
    assert "note.md" in payload["status"]
    assert "# Staged\n" in payload["diff"]
    assert "+已暂存的中文修改" in payload["diff"]
    assert "# Working tree\n" in payload["diff"]
    assert "+修改后的中文进展：研究成功。" in payload["diff"]
    assert response.headers["cache-control"] == "private, no-store"


def test_git_diff_replaces_non_utf8_bytes_without_losing_the_diff(git_workspace, monkeypatch) -> None:
    workspace, state, sid, _ = git_workspace
    (workspace / "note.md").write_bytes(b"legacy text: \xff\n")
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "utf-8")
    with TestClient(create_app(global_root=state)) as client:
        response = client.get(f"/api/projects/{sid}/git-diff")
    assert response.status_code == 200
    assert "+legacy text: \ufffd" in response.json()["diff"]
