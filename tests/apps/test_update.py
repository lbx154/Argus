from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import pytest

from argus_skill.apps.update import UpdateError, update_source_checkout


def _runner(
    responses: dict[tuple[str, ...], tuple[int, str, str]],
    calls: list[tuple[str, ...]],
):
    def run(
        command: Sequence[str],
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout
        key = tuple(command)
        calls.append(key)
        rc, stdout, stderr = responses[key]
        return subprocess.CompletedProcess(command, rc, stdout, stderr)

    return run


def test_update_fast_forwards_and_reinstalls(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus-skill'\n")
    python = "/venv/bin/python"
    calls: list[tuple[str, ...]] = []
    responses = {
        ("git", "rev-parse", "--show-toplevel"): (0, str(tmp_path), ""),
        ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "", ""),
        ("git", "branch", "--show-current"): (0, "main\n", ""),
        (
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ): (0, "origin/main\n", ""),
        ("git", "config", "--get", "branch.main.remote"): (0, "origin\n", ""),
        ("git", "config", "--get", "branch.main.merge"): (
            0,
            "refs/heads/main\n",
            "",
        ),
        ("git", "pull", "--ff-only", "origin", "refs/heads/main"): (
            0,
            "updated\n",
            "",
        ),
        (python, "-m", "pip", "install", "-e", str(tmp_path)): (0, "", ""),
    }
    revision_reads = 0

    def runner(
        command: Sequence[str],
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal revision_reads
        del cwd, timeout
        key = tuple(command)
        calls.append(key)
        if key == ("git", "rev-parse", "HEAD"):
            revision_reads += 1
            revision = "old\n" if revision_reads == 1 else "new\n"
            return subprocess.CompletedProcess(command, 0, revision, "")
        rc, stdout, stderr = responses[key]
        return subprocess.CompletedProcess(command, rc, stdout, stderr)

    result = update_source_checkout(
        tmp_path,
        runner=runner,
        python_executable=python,
    )

    assert result.changed is True
    assert result.upstream == "origin/main"
    assert (
        "git",
        "pull",
        "--ff-only",
        "origin",
        "refs/heads/main",
    ) in calls
    assert (python, "-m", "pip", "install", "-e", str(tmp_path)) in calls


def test_update_refuses_dirty_checkout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus-skill'\n")
    calls: list[tuple[str, ...]] = []
    runner = _runner(
        {
            ("git", "rev-parse", "--show-toplevel"): (0, str(tmp_path), ""),
            ("git", "status", "--porcelain", "--untracked-files=normal"): (
                0,
                " M README.md\n",
                "",
            ),
        },
        calls,
    )

    with pytest.raises(UpdateError, match="local changes"):
        update_source_checkout(tmp_path, runner=runner)

    assert ("git", "pull", "--ff-only") not in calls


def test_update_skips_reinstall_when_current(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus-skill'\n")
    python = "/venv/bin/python"
    calls: list[tuple[str, ...]] = []
    responses = {
        ("git", "rev-parse", "--show-toplevel"): (0, str(tmp_path), ""),
        ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "", ""),
        ("git", "branch", "--show-current"): (0, "main\n", ""),
        (
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ): (0, "origin/main\n", ""),
        ("git", "config", "--get", "branch.main.remote"): (0, "origin\n", ""),
        ("git", "config", "--get", "branch.main.merge"): (
            0,
            "refs/heads/main\n",
            "",
        ),
        ("git", "rev-parse", "HEAD"): (0, "same\n", ""),
        ("git", "pull", "--ff-only", "origin", "refs/heads/main"): (
            0,
            "Already up to date.\n",
            "",
        ),
    }

    result = update_source_checkout(
        tmp_path,
        runner=_runner(responses, calls),
        python_executable=python,
    )

    assert result.changed is False
    assert (python, "-m", "pip", "install", "-e", str(tmp_path)) not in calls
