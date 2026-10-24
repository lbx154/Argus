"""Tests for ``core.project`` — fingerprinting a working directory."""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from argus.core import paths as core_paths
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


# The name releases before this one gave the project labelled "/srv/papers/demo".
_LEGACY = "a38d4ed5e477"
_NAME = project._directory_name("/srv/papers/demo")


def test_both_project_state_directories_keep_the_new_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    states = core_paths.session_states_root(tmp_path)
    for name, text in ((_LEGACY, "old\n"), (_NAME, "new\n")):
        (states / name).mkdir(parents=True)
        (states / name / "backlog.jsonl").write_text(text, encoding="utf-8")

    state = core_paths.session_state_root(_NAME, root=tmp_path)

    assert state == states / _NAME
    assert (state / "backlog.jsonl").read_text(encoding="utf-8") == "new\n"
    assert (states / _LEGACY / "backlog.jsonl").read_text(encoding="utf-8") == "old\n"
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_legacy_project_state_with_a_running_daemon_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.core import project_gc

    states = core_paths.session_states_root(tmp_path)
    (states / _LEGACY).mkdir(parents=True)
    monkeypatch.setattr(project_gc, "_project_is_live", lambda _path: True)

    assert core_paths.session_state_root(_NAME, root=tmp_path) == states / _LEGACY
    assert not (states / _NAME).exists()

    monkeypatch.setattr(project_gc, "_project_is_live", lambda _path: False)
    assert core_paths.session_state_root(_NAME, root=tmp_path) == states / _NAME
    assert (states / _NAME).is_dir()
    assert not (states / _LEGACY).exists()


def test_a_path_saved_before_the_migration_finds_the_new_directory(tmp_path: Path) -> None:
    from argus.daemon.config import LifeWorkerConfig, config_from_payload, config_payload

    states = core_paths.session_states_root(tmp_path)
    (states / _LEGACY).mkdir(parents=True)
    saved = config_payload(LifeWorkerConfig(
        life_dir=states / _LEGACY, global_root=tmp_path, project_fingerprint=_LEGACY, backend="memory",
    ))
    core_paths.session_state_root(_NAME, root=tmp_path)

    assert json.loads((states / project.LEGACY_NAMES).read_text(encoding="utf-8")) == {_LEGACY: _NAME}
    restarted = config_from_payload(saved)
    assert restarted.life_dir == states / _NAME
    assert restarted.project_fingerprint == _NAME
    assert core_paths.session_state_root(_LEGACY, root=tmp_path) == states / _NAME
    assert project.resolve_legacy_project_state(states / _LEGACY) == states / _NAME
    assert sorted(path.name for path in states.iterdir()) == sorted(
        [_NAME, project.LEGACY_NAMES, ".legacy-names.lock"],
    )


def test_project_scans_ignore_the_legacy_name_table(tmp_path: Path) -> None:
    import time

    from argus.core.project_gc import gc_stale_projects
    from argus.core.session import list_sessions

    states = core_paths.session_states_root(tmp_path)
    (states / _LEGACY).mkdir(parents=True)
    core_paths.session_state_root(_NAME, root=tmp_path)

    assert [meta.id for meta in list_sessions(tmp_path)] == [_NAME]
    pruned = gc_stale_projects(tmp_path, retention_days=0, now=time.time() + 86400)
    assert pruned == [_NAME]
    assert sorted(path.name for path in states.iterdir()) == [project.LEGACY_NAMES, ".legacy-names.lock"]
