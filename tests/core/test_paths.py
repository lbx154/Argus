"""Tests for ``core.paths`` — the centralised on-disk layout."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.core import paths


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_HOME", raising=False)


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


def test_top_level_paths_compose_from_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    assert paths.identity_path() == tmp_path / "identity.md"
    assert paths.config_path() == tmp_path / "config.json"
    assert paths.shared_skills_root() == tmp_path / "skills"
    assert paths.shared_skills_archive_root() == tmp_path / "skills" / "_archive"
    assert paths.tools_root() == tmp_path / "tools"
    assert paths.capabilities_root() == tmp_path / "capabilities"
    assert paths.special_prompts_root() == tmp_path / "special_prompts"
    assert paths.logs_root() == tmp_path / "logs"
    assert paths.run_root() == tmp_path / "run"
    assert paths.session_states_root() == tmp_path / "projects"
    assert paths.session_trash_root() == tmp_path / "projects_trash"


def test_session_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    sid = "s-abc12345"
    assert paths.session_state_root(sid) == tmp_path / "projects" / sid
    assert paths.session_state_root(sid, root=tmp_path / "other") == (
        tmp_path / "other" / "projects" / sid
    )


@pytest.mark.parametrize(
    "bad",
    ["", "../escape", "/abs", "with/slash", r"with\\slash", ".hidden", "bad\x00id"],
)
def test_invalid_session_id_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        paths.session_state_root(bad)
