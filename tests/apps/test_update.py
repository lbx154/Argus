from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import pytest

from argus_skill.apps.update import (
    UpdateError,
    inspect_source_checkout,
    update_source_checkout,
)


def _runner(
    responses: dict[tuple[str, ...], tuple[int, str, str]],
    calls: list[tuple[str, ...]],
):
    def run(
        command: Sequence[str],
        cwd: Path,
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout
        key = tuple(command)
        calls.append(key)
        rc, stdout, stderr = responses[key]
        return subprocess.CompletedProcess(command, rc, stdout, stderr)

    return run


def test_update_pulls_matching_published_branch_and_reinstalls(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus-skill'\n")
    python = "/venv/bin/python"
    calls: list[tuple[str, ...]] = []
    responses = {
        ("git", "rev-parse", "--show-toplevel"): (0, str(tmp_path), ""),
        ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "", ""),
        ("git", "branch", "--show-current"): (0, "private-preview\n", ""),
        (
            "git",
            "pull",
            "--ff-only",
            "https://github.com/lbx154/Argus.git",
            "refs/heads/private-preview",
        ): (
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
        timeout: float | None,
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
    assert result.upstream == "lbx154/Argus/private-preview"
    assert (
        "git",
        "pull",
        "--ff-only",
        "https://github.com/lbx154/Argus.git",
        "refs/heads/private-preview",
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
        ("git", "rev-parse", "HEAD"): (0, "same\n", ""),
        (
            "git",
            "pull",
            "--ff-only",
            "https://github.com/lbx154/Argus.git",
            "refs/heads/main",
        ): (
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


def test_inspect_source_checkout_compares_matching_published_branch_without_mutation(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus-skill'\n")
    calls: list[tuple[str, ...]] = []
    responses = {
        ("git", "rev-parse", "--show-toplevel"): (0, str(tmp_path), ""),
        ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "", ""),
        ("git", "branch", "--show-current"): (0, "feature/live-lab\n", ""),
        ("git", "rev-parse", "HEAD"): (0, "old\n", ""),
        (
            "git",
            "ls-remote",
            "https://github.com/lbx154/Argus.git",
            "refs/heads/feature/live-lab",
        ): (0, "new\trefs/heads/feature/live-lab\n", ""),
    }

    result = inspect_source_checkout(tmp_path, runner=_runner(responses, calls))

    assert result.current_revision == "old"
    assert result.upstream_revision == "new"
    assert result.upstream == "lbx154/Argus/feature/live-lab"
    assert result.update_available is True
    assert result.can_update is True
    assert not any(command[:2] == ("git", "pull") for command in calls)
