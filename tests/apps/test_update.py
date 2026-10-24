from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Sequence

import pytest

from argus.apps import update
from argus.apps.update import (
    UpdateError,
    inspect_source_checkout,
    update_source_checkout,
)


@pytest.fixture(autouse=True)
def isolate_mock_installer_launchers(monkeypatch):
    monkeypatch.setattr(update, "preserve_windows_launchers", nullcontext)
    monkeypatch.setattr(update, "validate_pip_target", lambda *_args, **_kwargs: None)


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
        if key == ("git", "remote", "get-url", "origin") and key not in responses:
            return subprocess.CompletedProcess(command, 0, update.PUBLIC_REPOSITORY, "")
        if key[1:] == ("-m", "pip", "--version") and key not in responses:
            return subprocess.CompletedProcess(command, 0, "pip 24", "")
        rc, stdout, stderr = responses[key]
        return subprocess.CompletedProcess(command, rc, stdout, stderr)

    return run


def test_update_refuses_dirty_checkout(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus'\n")
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


@pytest.mark.parametrize("scenario", ["update", "diverged", "ahead", "install-failure"])
def test_source_updater_real_git_smoke_follows_branch_and_refuses_divergence(
    tmp_path: Path, monkeypatch, scenario: str,
) -> None:
    def git(root, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Argus test", "-c", "user.email=test@example.invalid", *args],
            cwd=root, text=True, capture_output=True, check=True,
        ).stdout.strip()

    upstream = tmp_path / "published"
    upstream.mkdir()
    git(upstream, "init", "-b", "feature/lab")
    (upstream / "pyproject.toml").write_text("[project]\nname='argus'\n")
    git(upstream, "add", "pyproject.toml")
    git(upstream, "commit", "-m", "initial")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(upstream), str(checkout))
    if scenario in {"diverged", "ahead"}:
        (checkout / "local.txt").write_text("local change")
        git(checkout, "add", "local.txt")
        git(checkout, "commit", "-m", "local")
    before = git(checkout, "rev-parse", "HEAD")
    if scenario != "ahead":
        (upstream / "published.txt").write_text("published change")
        git(upstream, "add", "published.txt")
        git(upstream, "commit", "-m", "published")
    published = git(upstream, "rev-parse", "HEAD")
    monkeypatch.setattr(update, "_published_source", lambda _root, _runner: (str(upstream), "lbx154/Argus"))
    check = inspect_source_checkout(checkout)
    assert check.branch == "feature/lab"
    assert check.upstream_revision == published
    installs = []
    def runner(command, cwd, timeout):
        if command[0] == "test-python":
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, "pip 24", "")
            installs.append(tuple(command))
            if scenario == "install-failure":
                return subprocess.CompletedProcess(command, 1, "", "installation failed")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.run(command, cwd=cwd, timeout=timeout, text=True, capture_output=True)

    if scenario == "diverged":
        with pytest.raises(UpdateError, match="fast-forward"):
            update_source_checkout(checkout, runner=runner, python_executable="test-python")
        assert git(checkout, "rev-parse", "HEAD") == before
        assert installs == []
    elif scenario == "ahead":
        with pytest.raises(UpdateError, match="unpublished local commits"):
            update_source_checkout(checkout, runner=runner, python_executable="test-python")
        assert git(checkout, "rev-parse", "HEAD") == before
        assert installs == []
    elif scenario == "install-failure":
        with pytest.raises(UpdateError, match="installation failed") as caught:
            update_source_checkout(checkout, runner=runner, python_executable="test-python")
        assert caught.value.result is not None
        assert caught.value.result.after_revision == published
        assert caught.value.result.changed is True
        assert git(checkout, "rev-parse", "HEAD") == published
        with pytest.raises(UpdateError, match="installation failed"):
            update_source_checkout(checkout, runner=runner, python_executable="test-python")
        assert len(installs) == 2
    else:
        result = update_source_checkout(checkout, runner=runner, python_executable="test-python")
        assert result.after_revision == published
        assert result.before_revision == before
        assert installs == [("test-python", "-m", "pip", "install", "-e", str(checkout))]


@pytest.mark.parametrize("remote", [
    "https://github.com/unknown/Argus.git", "https://github.com.evil.invalid/lbx154/Argus.git",
    "https://user:secret@github.com/lbx154/Argus.git", "file:///tmp/Argus",
    "https://github.com/lbx154/Argus.git?replace=1", "https://github.com:invalid/lbx154/Argus.git",
])
def test_source_channel_refuses_untrusted_origin(tmp_path, remote):
    runner = _runner({("git", "remote", "get-url", "origin"): (0, remote, "")}, [])
    with pytest.raises(UpdateError, match="unsupported source origin"):
        update._published_source(tmp_path, runner)


def test_source_update_supports_uv_without_pip(tmp_path, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda _name: "uv")
    calls = []
    runner = _runner({
        ("python", "-m", "pip", "--version"): (1, "", "No module named pip"),
        ("uv", "--version"): (0, "uv 0.10", ""),
    }, calls)
    assert update._editable_install_command(tmp_path, "python", runner) == [
        "uv", "pip", "install", "--python", "python", "-e", str(tmp_path),
    ]


def test_source_update_missing_installer_does_not_pull(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='argus'\n")
    monkeypatch.setattr(update.shutil, "which", lambda _name: None)
    calls = []
    runner = _runner({
        ("git", "rev-parse", "--show-toplevel"): (0, str(tmp_path), ""),
        ("git", "status", "--porcelain", "--untracked-files=normal"): (0, "", ""),
        ("git", "branch", "--show-current"): (0, "main", ""),
        ("python", "-m", "pip", "--version"): (1, "", "No module named pip"),
    }, calls)
    with pytest.raises(UpdateError, match="no working pip"):
        update_source_checkout(tmp_path, runner=runner, python_executable="python")
    assert not any(command[:2] == ("git", "pull") for command in calls)
