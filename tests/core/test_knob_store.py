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

from argus.core import knob_store


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-skill-home"))


def test_backend_switch_clears_its_persisted_runner_bin() -> None:
    knob_store.write_persisted_knobs(
        {"ARGUS_SKILL_RUNNER_BACKEND": "dsh", "ARGUS_SKILL_RUNNER_BIN": "/opt/bin/dsh"}
    )
    knob_store.write_persisted_knobs({"ARGUS_SKILL_MODEL": "same-backend"})
    assert knob_store.read_persisted_knobs()["ARGUS_SKILL_RUNNER_BIN"] == "/opt/bin/dsh"

    knob_store.write_persisted_knobs({"ARGUS_SKILL_RUNNER_BACKEND": "copilot"})

    assert knob_store.read_persisted_knobs()["ARGUS_SKILL_RUNNER_BIN"] == ""


def test_read_persisted_knobs_raises_on_non_dict_json():
    """Valid JSON of the wrong shape is the same failure, and used to be even
    quieter: it returned {} without so much as a warning."""
    from argus.core.paths import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(knob_store.KnobStoreCorruptError):
        knob_store.read_persisted_knobs()


def test_write_persisted_knob_empty_name_is_a_noop():
    assert knob_store.write_persisted_knob("", "value") is False
    assert knob_store.read_persisted_knobs() == {}
