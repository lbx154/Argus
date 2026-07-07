"""Tests for ``core.paths`` — the centralised on-disk layout."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.core import paths


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_HOME", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_DIR", raising=False)


def test_default_root_is_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.global_root() == tmp_path / ".argus-skill"


def test_argus_skill_home_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "alt"))
    assert paths.global_root() == tmp_path / "alt"


def test_argus_skill_home_expands_shell_placeholders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path / "expanded"))
    monkeypatch.setenv("ARGUS_SKILL_HOME", "$TMPDIR")
    assert paths.global_root() == tmp_path / "expanded"


def test_argus_skill_home_rejects_unresolved_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setenv("ARGUS_SKILL_HOME", "$TMPDIR")
    with pytest.raises(paths.PathResolutionError):
        paths.global_root()


def test_legacy_life_dir_pointing_at_life_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_DIR", str(tmp_path / "legacy" / "life"))
    assert paths.global_root() == tmp_path / "legacy"


def test_legacy_life_dir_pointing_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LIFE_DIR", str(tmp_path / "weird"))
    assert paths.global_root() == tmp_path / "weird"


def test_argus_skill_home_takes_precedence_over_legacy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "new"))
    monkeypatch.setenv("ARGUS_SKILL_LIFE_DIR", str(tmp_path / "old" / "life"))
    assert paths.global_root() == tmp_path / "new"


def test_top_level_paths_compose_from_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    assert paths.identity_path() == tmp_path / "identity.md"
    assert paths.config_path() == tmp_path / "config.json"
    assert paths.journal_path() == tmp_path / "journal.jsonl"
    assert paths.bus_root() == tmp_path / "bus"
    assert paths.commands_path() == tmp_path / "bus" / "commands.jsonl"
    assert paths.outbox_path() == tmp_path / "bus" / "outbox.jsonl"
    assert paths.status_path() == tmp_path / "bus" / "status.json"
    assert paths.daemon_pid_path() == tmp_path / "bus" / "daemon.pid"
    assert paths.skills_global_root() == tmp_path / "skills"
    assert paths.skills_archive_root() == tmp_path / "skills" / "_archive"
    assert paths.projects_root() == tmp_path / "projects"


def test_project_subtree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    fp = "abc123def456"
    assert paths.project_root(fp) == tmp_path / "projects" / fp
    assert paths.project_memory_path(fp) == tmp_path / "projects" / fp / "events.jsonl"
    assert paths.project_backlog_path(fp) == tmp_path / "projects" / fp / "backlog.jsonl"
    assert paths.project_skills_root(fp) == tmp_path / "projects" / fp / "skills"
    assert paths.project_missions_root(fp) == tmp_path / "projects" / fp / "missions"
    assert paths.mission_root(fp, "m-001") == tmp_path / "projects" / fp / "missions" / "m-001"


@pytest.mark.parametrize("bad", ["", "../escape", "/abs", "with/slash", ".hidden"])
def test_invalid_fingerprint_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        paths.project_root(bad)


@pytest.mark.parametrize("bad", ["", "../escape", "with/slash", ".hidden"])
def test_invalid_mission_id_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        paths.mission_root("validfp1234", bad)


def test_ensure_dir_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    assert paths.ensure_dir(target) == target
    assert target.is_dir()
    assert paths.ensure_dir(target) == target
