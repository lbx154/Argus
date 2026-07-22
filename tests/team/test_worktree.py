from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus_skill.team import worktree as wt

INVALID_IDS = ["", ".", "..", "../escape", "a/b", "a\\b", "bad\x00id"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@pytest.mark.parametrize("bad_id", INVALID_IDS)
def test_path_for_rejects_invalid_team_ids(repo: Path, bad_id: str) -> None:
    with pytest.raises(ValueError):
        wt.path_for(repo, bad_id, "tm-1")


@pytest.mark.parametrize("bad_id", INVALID_IDS)
def test_path_for_rejects_invalid_member_ids(repo: Path, bad_id: str) -> None:
    with pytest.raises(ValueError):
        wt.path_for(repo, "t1", bad_id)


def test_create_rejects_invalid_team_id_without_escape(repo: Path) -> None:
    with pytest.raises(ValueError):
        wt.create(repo, team_id="../escape", member_id="tm-1")

    assert not (repo / "escape").exists()


def test_create_rejects_invalid_member_id_without_escape(repo: Path) -> None:
    with pytest.raises(ValueError):
        wt.create(repo, team_id="t1", member_id="../escape")

    assert not (repo / ".argus_team" / "t1" / "escape").exists()


def test_create_makes_isolated_worktree(repo: Path) -> None:
    p = wt.create(repo, team_id="t1", member_id="tm-1")
    assert p.exists() and (p / "README.md").exists()
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo,
                             capture_output=True, text=True).stdout
    assert str(p) in listing
    # writing in one worktree does not touch the main tree
    (p / "only_here.txt").write_text("hi", encoding="utf-8")
    assert not (repo / "only_here.txt").exists()


def test_remove_cleans_up(repo: Path) -> None:
    p = wt.create(repo, team_id="t1", member_id="tm-1")
    wt.remove(repo, p)
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo,
                             capture_output=True, text=True).stdout
    assert str(p) not in listing
