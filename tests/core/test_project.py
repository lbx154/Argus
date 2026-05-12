"""Tests for ``core.project`` — fingerprinting a working directory."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus_skill.core import project


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


def test_git_remote_drives_fingerprint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:foo/bar.git")

    identity = project.project_fingerprint(repo)
    assert identity.source == "git-remote"
    assert identity.label == "github.com/foo/bar"
    assert len(identity.fingerprint) == 12
    assert identity.cwd == str(repo.resolve())


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


def test_two_clones_of_same_remote_collide(tmp_path: Path) -> None:
    repo_a = tmp_path / "clone_a"
    repo_b = tmp_path / "clone_b"
    _git_init(repo_a)
    _git_init(repo_b)
    _git(repo_a, "remote", "add", "origin", "git@github.com:foo/bar.git")
    _git(repo_b, "remote", "add", "origin", "https://github.com/foo/bar")

    fa = project.project_fingerprint(repo_a)
    fb = project.project_fingerprint(repo_b)
    assert fa.fingerprint == fb.fingerprint
    assert fa.source == fb.source == "git-remote"


def test_different_remotes_distinct_fingerprints(tmp_path: Path) -> None:
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    _git_init(repo_a)
    _git_init(repo_b)
    _git(repo_a, "remote", "add", "origin", "git@github.com:foo/bar.git")
    _git(repo_b, "remote", "add", "origin", "git@github.com:foo/baz.git")
    assert (
        project.project_fingerprint(repo_a).fingerprint
        != project.project_fingerprint(repo_b).fingerprint
    )


def test_non_git_dir_falls_back_to_cwd(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    identity = project.project_fingerprint(plain)
    assert identity.source == "cwd-path"
    assert identity.label == str(plain.resolve())
    assert len(identity.fingerprint) == 12


def test_git_repo_without_remote_falls_back_to_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "no_remote"
    _git_init(repo)
    identity = project.project_fingerprint(repo)
    assert identity.source == "cwd-path"
    assert identity.label == str(repo.resolve())


def test_fallback_stable_across_calls(tmp_path: Path) -> None:
    a = project.project_fingerprint(tmp_path).fingerprint
    b = project.project_fingerprint(tmp_path).fingerprint
    assert a == b
