"""skill_tidy's end-of-mission auto-commit must be SAFE for the operator's repo:
OFF by default, and when enabled it commits ONLY the skill paths — never the
operator's ambient hand-staged index. (Roadmap: careful-hunt critical finding.)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from argus_skill.manager.skill_tidy import commit_to_source


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


def test_autocommit_is_off_by_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", raising=False)
    p = tmp_path / "skill.md"
    p.write_text("x", encoding="utf-8")
    # Default OFF → does not commit (returns False), and never even touches git.
    assert commit_to_source([p], "chore: skill") is False


def test_enabled_commit_only_touches_given_paths_not_the_staged_index(
    monkeypatch, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git("config", "user.email", "t@t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "seed.txt").write_text("seed", encoding="utf-8")
    _git("add", "seed.txt", cwd=tmp_path)
    _git("commit", "-qm", "seed", cwd=tmp_path)

    # The operator has hand-staged work in the same repo.
    (tmp_path / "operator_wip.txt").write_text("hand-staged", encoding="utf-8")
    _git("add", "operator_wip.txt", cwd=tmp_path)

    # A distilled skill file to be auto-committed.
    skill = tmp_path / "skill.md"
    skill.write_text("skill", encoding="utf-8")

    monkeypatch.setattr(
        "argus_skill.manager.skill_tidy._argus_source_root", lambda: tmp_path
    )
    monkeypatch.setenv("ARGUS_SKILL_AUTOCOMMIT_SKILLS", "1")

    assert commit_to_source([skill], "chore(skills): tidy") is True

    # The new commit contains ONLY skill.md — the operator's hand-staged file is
    # NOT swept in.
    committed = _git("show", "--name-only", "--format=", "HEAD", cwd=tmp_path).split()
    assert "skill.md" in committed
    assert "operator_wip.txt" not in committed
    # ...and the operator's file is still staged, uncommitted, intact.
    assert "operator_wip.txt" in _git(
        "diff", "--cached", "--name-only", cwd=tmp_path
    )
