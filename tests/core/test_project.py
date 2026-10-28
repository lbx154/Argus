"""Tests for ``core.project`` — fingerprinting a working directory."""
from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from argus.core import project


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://github.com/foo/bar.git", "github.com/foo/bar"),
        ("https://github.com/foo/bar/", "github.com/foo/bar"),
        ("https://github.com/foo/bar", "github.com/foo/bar"),
        ("HTTPS://GitHub.com/Foo/Bar.GIT", "github.com/foo/bar"),
        ("git@github.com:foo/bar.git", "github.com/foo/bar"),
        ("git@github.com:foo/bar", "github.com/foo/bar"),
        ("ssh://git@github.com/foo/bar.git", "github.com/foo/bar"),
        ("ssh://git@github.com:22/foo/bar.git", "github.com:22/foo/bar"),
        ("git://github.com/foo/bar.git", "github.com/foo/bar"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_collapses_variants(raw: str, expected: str) -> None:
    assert project.normalize_git_remote(raw) == expected


def test_normalize_idempotent() -> None:
    once = project.normalize_git_remote("git@github.com:foo/bar.git")
    twice = project.normalize_git_remote(once)
    assert once == twice == "github.com/foo/bar"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=project._git_env(),
        check=True,
        capture_output=True,
    )


def _git_init(cwd: Path) -> None:
    cwd.mkdir(parents=True, exist_ok=True)
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "test@example.com")
    _git(cwd, "config", "user.name", "test")


def test_git_remote_drives_fingerprint_with_broken_git_config_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "explicit")
    monkeypatch.delenv("GIT_CONFIG_KEY_0", raising=False)
    _git_init(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:foo/bar.git")

    identity = project.project_fingerprint(repo)
    assert identity.source == "git-remote"
    assert identity.label == "github.com/foo/bar"


def test_distinct_paths_never_share_a_directory_name() -> None:
    labels = [
        "/a/b-c", "/a/b/c", "/a_b", "/a/b", "/a%2Fb", "/a.b", "/a%2Eb",
        "github.com/foo/bar", "/github.com/foo/bar", "../shared", "C:\\论文 进度",
    ]
    names = [project._directory_name(label) for label in labels]

    assert len(set(names)) == len(labels)
    assert [unquote(name) for name in names] == labels
    assert not any(name.startswith(".") or name.endswith(".") or "/" in name for name in names)


def test_overlong_project_path_is_refused() -> None:
    with pytest.raises(ValueError, match="too long"):
        project._directory_name("/" + "x" * 255)
