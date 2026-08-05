"""Tests for the LLM config-intent classifier (life/router.classify_config_intent).

The classifier is a pure parser over one low-reasoning model call: given the
model's "SET <knob> <roles> <value>" / "NONE" answer, it returns a validated
``ConfigIntent`` or ``None``. These tests drive it with a fake ``run_exec`` so
they assert the PARSING + validation (no real model), the same way the rest of
the front-door router is tested. There is no keyword/regex path to test — intent
recognition is the model's job; this layer only validates its structured reply.
"""
from __future__ import annotations

import pytest

from argus_skill.life.router import (
    ConfigIntent,
    build_config_intent_prompt,
    classify_config_intent,
)


class _FakeResult:
    def __init__(self, msg: str, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.last_agent_message = msg


def _exec(answer: str, exit_code: int = 0):
    def run_exec(prompt: str):
        assert "SET <knob> <roles> <value>" in prompt  # the real prompt is passed
        return _FakeResult(answer, exit_code)

    return run_exec


# ── role-scoped knobs: backend / model / effort ───────────────────────────────

def test_model_role_specific() -> None:
    intent = classify_config_intent("x", run_exec=_exec("SET model engineer claude-sonnet-5"))
    assert intent == ConfigIntent(knob="model", roles=("engineer",), value="claude-sonnet-5")


def test_backend_all_becomes_empty_roles() -> None:
    intent = classify_config_intent("x", run_exec=_exec("SET backend ALL codex"))
    assert intent == ConfigIntent(knob="backend", roles=(), value="codex")


def test_backend_and_model_batch() -> None:
    intent = classify_config_intent(
        "x",
        run_exec=_exec("SET backend ALL pi; SET model ALL gpt5.6sol"),
    )
    assert intent == (
        ConfigIntent(knob="backend", roles=(), value="pi"),
        ConfigIntent(knob="model", roles=(), value="gpt5.6sol"),
    )


def test_effort_multiple_roles() -> None:
    intent = classify_config_intent("x", run_exec=_exec("SET effort engineer,reviewer high"))
    assert intent is not None
    assert intent.knob == "effort" and intent.value == "high"
    assert set(intent.roles) == {"engineer", "reviewer"}


def test_unknown_roles_filtered_out_and_none_left_is_none() -> None:
    # A role token that is not one of the four real roles is dropped; if nothing
    # valid remains, the whole intent is rejected (safe: no accidental apply).
    assert classify_config_intent("x", run_exec=_exec("SET model banana gpt-5.5")) is None


# ── global knobs: one budget cap + toggles (roles field is a dash) ────────────

def test_global_daily_cap() -> None:
    intent = classify_config_intent("x", run_exec=_exec("SET global_daily_cap - 200"))
    assert intent == ConfigIntent(knob="global_daily_cap", roles=(), value="200")


@pytest.mark.parametrize("retired", ["per_mission_cap", "daily_cap"])
def test_retired_budget_knobs_are_rejected(retired: str) -> None:
    assert classify_config_intent(
        "x",
        run_exec=_exec(f"SET {retired} - 50"),
    ) is None


@pytest.mark.parametrize(
    ("knob", "value"),
    [
        ("max_daemons", "4"),
        ("codex_daily_requests", "250"),
        ("copilot_daily_requests", "180"),
        ("copilot_daily_premium", "75"),
    ],
)
def test_daemon_and_provider_quota_globals(knob: str, value: str) -> None:
    intent = classify_config_intent("x", run_exec=_exec(f"SET {knob} - {value}"))
    assert intent == ConfigIntent(knob=knob, roles=(), value=value)


@pytest.mark.parametrize("knob", ["safe_mode", "show_reasoning", "telegram"])
def test_toggles_global(knob: str) -> None:
    intent = classify_config_intent("x", run_exec=_exec(f"SET {knob} - on"))
    assert intent == ConfigIntent(knob=knob, roles=(), value="on")


def test_global_knob_ignores_a_stray_role_field() -> None:
    # Global knobs carry no role; whatever the model puts in the roles slot is
    # ignored (it should send "-", but a spurious "all" must not break parsing).
    intent = classify_config_intent("x", run_exec=_exec("SET safe_mode all off"))
    assert intent == ConfigIntent(knob="safe_mode", roles=(), value="off")


# ── NONE / malformed / error → None (bias hard toward "not a config change") ──

def test_none_answer() -> None:
    assert classify_config_intent("train a model on imagenet", run_exec=_exec("NONE")) is None


@pytest.mark.parametrize("answer", [
    "",                       # empty
    "yes please",             # not a SET line
    "SET model engineer",     # missing value
    "SET frobnicate all x",   # unknown knob
    "SET model all ``",       # empty value after stripping quotes
    "GET model engineer x",   # wrong verb
])
def test_malformed_answers_are_none(answer: str) -> None:
    assert classify_config_intent("x", run_exec=_exec(answer)) is None


def test_nonzero_exit_code_is_none() -> None:
    assert classify_config_intent("x", run_exec=_exec("SET model engineer o3", exit_code=1)) is None


def test_run_exec_raising_is_none() -> None:
    def boom(prompt: str):
        raise RuntimeError("backend down")

    assert classify_config_intent("x", run_exec=boom) is None


def test_blank_input_short_circuits_without_calling_the_model() -> None:
    called = False

    def run_exec(prompt: str):
        nonlocal called
        called = True
        return _FakeResult("SET model engineer o3")

    assert classify_config_intent("   ", run_exec=run_exec) is None
    assert called is False


def test_value_quotes_are_stripped() -> None:
    intent = classify_config_intent("x", run_exec=_exec('SET model engineer "gpt-5.5"'))
    assert intent is not None and intent.value == "gpt-5.5"


def test_prompt_lists_all_knobs() -> None:
    prompt = build_config_intent_prompt("switch engineer to claude")
    for knob in (
        "backend", "model", "effort", "global_daily_cap",
        "max_daemons", "codex_daily_requests", "copilot_daily_requests",
        "copilot_daily_premium", "safe_mode", "show_reasoning", "telegram",
    ):
        assert knob in prompt
    assert "NONE" in prompt


def test_prompt_does_not_treat_single_run_budget_as_global_config() -> None:
    prompt = build_config_intent_prompt("这轮 mission 就给 200 刀,烧完自动停")
    low = prompt.lower()
    assert "global_daily_cap" in low
    assert "one specific run" in low
    assert "part of the task" in low
