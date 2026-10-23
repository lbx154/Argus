"""Chat setting changes: each setting stands alone, vetted against the right backend."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from argus.core.knob_store import read_persisted_knobs
from argus.manager.config_intent import (
    _ROLE_EFFORT_ENVS,
    _ROLE_MODEL_ENVS,
    _apply_config_intent,
)
from argus.manager.form import ConfigIntent

_ENV = (
    "ARGUS_SKILL_MODEL", "ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_LIFE_BACKEND",
    *_ROLE_EFFORT_ENVS.values(), *_ROLE_MODEL_ENVS.values(),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    return SimpleNamespace(project=SimpleNamespace(root=tmp_path), project_root=tmp_path)


_LISTS = {"codex": ["house-a", "house-b"], "copilot": ["hosted-x", "hosted-y"], "pi": ["local-m"]}


def _catalog(backend: str) -> list[str]:
    return _LISTS.get(backend, [])


def test_unknown_model_does_not_drop_other_settings_in_the_same_message(home) -> None:
    intent = (ConfigIntent("effort", (), "high"), ConfigIntent("model", (), "nope-1"))
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    stored = read_persisted_knobs()
    assert "ARGUS_SKILL_MODEL" not in stored
    assert any(v == "high" for k, v in stored.items() if "EFFORT" in k)
    assert any("nope-1" in line and "house-a" in line for line in lines)
    assert any("effort" in line.lower() and "high" in line for line in lines)


def test_model_is_vetted_against_the_backend_switched_to_in_the_same_message(home) -> None:
    intent = (ConfigIntent("backend", (), "copilot"), ConfigIntent("model", (), "hosted-y"))
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    stored = read_persisted_knobs()
    assert stored["ARGUS_SKILL_MODEL"] == "hosted-y"
    assert stored["ARGUS_SKILL_RUNNER_BACKEND"] == "copilot"

    # The old backend's model is not valid on the new one.
    again = (ConfigIntent("backend", (), "copilot"), ConfigIntent("model", (), "house-a"))
    assert _apply_config_intent(home, again, {}, on_confirm=lines.append, model_catalog=_catalog)
    assert read_persisted_knobs()["ARGUS_SKILL_MODEL"] == "hosted-y"
    assert any("house-a" in line and "copilot" in line for line in lines)


def test_role_model_is_vetted_against_the_shared_backend(home, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PLANNER_BACKEND", "pi")  # retired, ignored
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "pi")
    intent = ConfigIntent(knob="model", roles=("planner",), value="local-m")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    assert read_persisted_knobs()[_ROLE_MODEL_ENVS["planner"]] == "local-m", lines
    assert os.environ[_ROLE_MODEL_ENVS["planner"]] == "local-m"

    other = ConfigIntent(knob="model", roles=("planner",), value="house-a")
    assert _apply_config_intent(home, other, {}, on_confirm=lines.append, model_catalog=_catalog)
    assert read_persisted_knobs()[_ROLE_MODEL_ENVS["planner"]] == "local-m"
    assert "pi" in lines[-1]


def test_a_backend_change_is_one_setting_for_every_role(home) -> None:
    intent = ConfigIntent("backend", (), "copilot")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    stored = read_persisted_knobs()
    assert stored["ARGUS_SKILL_RUNNER_BACKEND"] == "copilot"
    assert "ARGUS_SKILL_REVIEWER_BACKEND" not in stored
    assert lines == ["Set all Argus roles' CLI backend to copilot."]


def test_refusal_names_the_backend_whose_list_lacks_the_model(home) -> None:
    intent = ConfigIntent(knob="model", roles=(), value="hosted-x")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    assert "ARGUS_SKILL_MODEL" not in read_persisted_knobs()
    assert len(lines) == 1 and "codex" in lines[0]


def test_config_change_grounding_note_is_tagged_as_config(home, tmp_path) -> None:
    import json

    intent = ConfigIntent("effort", (), "high")
    assert _apply_config_intent(home, intent, {}, on_confirm=lambda _line: None, model_catalog=_catalog)

    notes = [
        json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()
        if '"user.note"' in line
    ]
    assert notes and all(note["tags"] == ["config"] for note in notes)
