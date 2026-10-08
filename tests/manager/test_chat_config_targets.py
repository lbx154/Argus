"""Chat setting changes: each setting stands alone, vetted against the right backend."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from argus.core.knob_store import read_persisted_knobs
from argus.life.router import ConfigIntent, _parse_config_decision
from argus.manager.config_intent import (
    _ROLE_BACKEND_ENVS,
    _ROLE_EFFORT_ENVS,
    _ROLE_MODEL_ENVS,
    _apply_config_intent,
)

_ENV = (
    "ARGUS_SKILL_MODEL", "ARGUS_SKILL_RUNNER_BACKEND", "ARGUS_SKILL_LIFE_BACKEND",
    *_ROLE_BACKEND_ENVS.values(), *_ROLE_EFFORT_ENVS.values(), *_ROLE_MODEL_ENVS.values(),
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    return SimpleNamespace(project=SimpleNamespace(root=tmp_path))


_LISTS = {"codex": ["house-a", "house-b"], "copilot": ["hosted-x", "hosted-y"], "pi": ["local-m"]}


def _catalog(backend: str) -> list[str]:
    return _LISTS.get(backend, [])


def test_unknown_model_does_not_drop_other_settings_in_the_same_message(home) -> None:
    intent = _parse_config_decision("SET effort ALL high; SET model ALL nope-1")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    stored = read_persisted_knobs()
    assert "ARGUS_SKILL_MODEL" not in stored
    assert any(v == "high" for k, v in stored.items() if "EFFORT" in k)
    assert any("nope-1" in line and "house-a" in line for line in lines)
    assert any("effort" in line.lower() and "high" in line for line in lines)


def test_model_is_vetted_against_the_backend_switched_to_in_the_same_message(home) -> None:
    intent = _parse_config_decision("SET backend ALL copilot; SET model ALL hosted-y")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    stored = read_persisted_knobs()
    assert stored["ARGUS_SKILL_MODEL"] == "hosted-y"
    assert stored["ARGUS_SKILL_RUNNER_BACKEND"] == "copilot"

    # The old backend's model is not valid on the new one.
    again = _parse_config_decision("SET backend ALL copilot; SET model ALL house-a")
    assert _apply_config_intent(home, again, {}, on_confirm=lines.append, model_catalog=_catalog)
    assert read_persisted_knobs()["ARGUS_SKILL_MODEL"] == "hosted-y"
    assert any("house-a" in line and "copilot" in line for line in lines)


def test_role_model_is_vetted_against_that_roles_own_backend(home, monkeypatch) -> None:
    monkeypatch.setenv(_ROLE_BACKEND_ENVS["planner"], "pi")
    intent = ConfigIntent(knob="model", roles=("planner",), value="local-m")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    assert read_persisted_knobs()[_ROLE_MODEL_ENVS["planner"]] == "local-m", lines
    assert os.environ[_ROLE_MODEL_ENVS["planner"]] == "local-m"

    # The shared backend's model is not one the planner's backend accepts.
    other = ConfigIntent(knob="model", roles=("planner",), value="house-a")
    assert _apply_config_intent(home, other, {}, on_confirm=lines.append, model_catalog=_catalog)
    assert read_persisted_knobs()[_ROLE_MODEL_ENVS["planner"]] == "local-m"
    assert "pi" in lines[-1]


def test_refusal_names_the_backend_whose_list_lacks_the_model(home) -> None:
    intent = ConfigIntent(knob="model", roles=(), value="hosted-x")
    lines: list[str] = []

    assert _apply_config_intent(home, intent, {}, on_confirm=lines.append, model_catalog=_catalog)

    assert "ARGUS_SKILL_MODEL" not in read_persisted_knobs()
    assert len(lines) == 1 and "codex" in lines[0]


@pytest.mark.parametrize(
    ("line", "knob", "value"),
    [
        ("SET telegram - on for ALL", "telegram", "on"),
        ("SET safe_mode - to off", "safe_mode", "off"),
        ("SET global_daily_cap - 50 USD", "global_daily_cap", "50 USD"),
    ],
)
def test_global_settings_shed_filler_words(line: str, knob: str, value: str) -> None:
    assert _parse_config_decision(line) == ConfigIntent(knob=knob, roles=(), value=value)


def test_telegram_chat_vets_models_like_the_web_chat(tmp_path, monkeypatch, home) -> None:
    from argus.life.chat import router as chat_router
    from argus.webapi import mission_items

    life_dir = tmp_path / "projects" / "p1"
    life_dir.mkdir(parents=True)
    sent: list[str] = []
    transport = SimpleNamespace(channel="telegram", send=sent.append)
    router = chat_router.CommandRouter(life_dir=life_dir, transport=transport)
    intent = ConfigIntent(knob="model", roles=(), value="nope-1")
    monkeypatch.setattr(router, "_intake_operator_text", lambda text: (text, intent, None, "simple"))
    monkeypatch.setattr(mission_items, "backend_model_ids", lambda backend, _root=None: _catalog(backend))

    router._cmd_free_text("switch every role to nope-1")

    assert "ARGUS_SKILL_MODEL" not in read_persisted_knobs()
    assert sent and "nope-1" in sent[-1] and "house-a" in sent[-1]
