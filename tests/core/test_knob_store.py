"""Tests for ``core.knob_store`` — persisted operator knob overrides.

A ``/backend``/``/config`` or natural-language hyperparameter switch used to
only set ``os.environ`` for the CURRENT process — a restart (of the REPL, or
the next time the daemon boots) silently reverted to the default. This module
is the persisted layer that makes "change it once, read it consistently from
then on" actually true; see ``core.knobs.resolve_role_model`` for the
resolver that consumes it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.core import knob_store


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-skill-home"))


def test_read_persisted_knobs_empty_when_missing() -> None:
    assert knob_store.read_persisted_knobs() == {}


def test_write_then_read_roundtrips() -> None:
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "gpt-5.5")
    knob_store.write_persisted_knob("ARGUS_SKILL_MANAGER_BACKEND", "copilot")
    assert knob_store.read_persisted_knobs() == {
        "ARGUS_SKILL_MODEL": "gpt-5.5",
        "ARGUS_SKILL_MANAGER_BACKEND": "copilot",
    }


def test_write_persisted_knob_overwrites_only_that_key() -> None:
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "gpt-5.5")
    knob_store.write_persisted_knob("ARGUS_SKILL_MANAGER_BACKEND", "copilot")
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    assert knob_store.read_persisted_knobs() == {
        "ARGUS_SKILL_MODEL": "claude-sonnet-5",
        "ARGUS_SKILL_MANAGER_BACKEND": "copilot",
    }


def test_write_persisted_knob_is_atomic_no_tmp_file_left_behind():
    from argus_skill.core.paths import config_path

    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "gpt-5.5")
    path = config_path()
    assert path.exists()
    leftover_tmp = list(path.parent.glob("*.tmp"))
    assert leftover_tmp == [], f"atomic write left a temp file behind: {leftover_tmp}"


def test_read_persisted_knobs_tolerates_malformed_json():
    from argus_skill.core.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert knob_store.read_persisted_knobs() == {}


def test_read_persisted_knobs_tolerates_non_dict_json():
    from argus_skill.core.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert knob_store.read_persisted_knobs() == {}


def test_write_persisted_knob_empty_name_is_a_noop():
    knob_store.write_persisted_knob("", "value")
    assert knob_store.read_persisted_knobs() == {}


def test_persisted_knob_env_wins_over_persisted_file():
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    assert knob_store.persisted_knob(
        "ARGUS_SKILL_MODEL", env={"ARGUS_SKILL_MODEL": "gpt-5.4-mini"},
    ) == "gpt-5.4-mini"


def test_persisted_knob_falls_back_to_file_when_env_unset():
    knob_store.write_persisted_knob("ARGUS_SKILL_MODEL", "claude-sonnet-5")
    assert knob_store.persisted_knob("ARGUS_SKILL_MODEL", env={}) == "claude-sonnet-5"


def test_persisted_knob_empty_when_neither_set():
    assert knob_store.persisted_knob("ARGUS_SKILL_MODEL", env={}) == ""
