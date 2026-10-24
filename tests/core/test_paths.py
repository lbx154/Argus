"""Tests for ``core.paths`` — the centralised on-disk layout."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.core import paths


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS_SKILL_HOME", raising=False)


def test_default_root_is_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.global_root() == tmp_path / ".argus-skill"


def test_argus_skill_home_expands_shell_placeholders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path / "expanded"))
    monkeypatch.setenv("ARGUS_SKILL_HOME", "$TMPDIR")
    assert paths.global_root() == tmp_path / "expanded"


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
    assert paths.session_states_root() == tmp_path / "projects"
    assert paths.session_trash_root() == tmp_path / "projects_trash"


@pytest.mark.parametrize(
    "bad",
    ["", "../escape", "/abs", "with/slash", r"with\\slash", ".hidden", "bad\x00id"],
)
def test_invalid_session_id_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        paths.session_state_root(bad)
