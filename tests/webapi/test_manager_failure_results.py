"""Plugin consumers need error kinds, not success-shaped chat on host failures."""
from __future__ import annotations

import pytest

from argus_skill.manager import front_door
from argus_skill.webapi.manager_dispatch import _run_triage_and_fallbacks, _TurnEmitter


@pytest.fixture
def emitter(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_WORKBENCH_HOST_ROOT", str(tmp_path))
    reviews = []
    value = _TurnEmitter(life_dir=tmp_path, turn_id="fixture-turn", fragment=lambda *_: None, after_reply=reviews.append)
    return value, reviews


def dispatch(emitter, state=None, *, failure="", route="simple", control=None):
    return _run_triage_and_fallbacks(object(), "Synthetic request", state if state is not None else {},
                                    route, control, "fixture-task", failure, None, emitter)


def test_classification_failure_is_an_error_without_another_call_or_learning_review(emitter, monkeypatch):
    output, reviews = emitter
    monkeypatch.setattr(front_door, "manager_triage", lambda *a, **k: pytest.fail("Do not try a second request"))
    result = dispatch(output, failure="Synthetic classifier failure", route="complex")
    assert result["kind"] == "error"
    assert result["success"] is False
    assert result["error_code"] == "classification_failed"
    assert not reviews


@pytest.mark.parametrize("control", [None, "no_dispatch"])
def test_unavailable_inline_reply_is_not_a_completed_plugin_turn(emitter, monkeypatch, control):
    output, reviews = emitter
    monkeypatch.setattr(front_door, "manager_triage", lambda *a, **k: None)
    result = dispatch(output, control=control)
    assert result["kind"] == "error" and result["success"] is False
    assert result["error_code"] == "inline_reply_failed"
    assert not reviews


def test_explicit_self_failure_is_not_changed_to_success_by_a_reply_string(emitter, monkeypatch):
    output, reviews = emitter
    def failed(mem, body, state, **kwargs):
        state["_self_failure"] = {"detail": "Synthetic request failed"}
        state["_self_delivery"] = {"summary": "must not publish this"}
        return "Synthetic request failed"
    monkeypatch.setattr(front_door, "manager_triage", failed)
    result = dispatch(output)
    assert result["kind"] == "error" and result["success"] is False
    assert "delivery" not in result and not result.get("mission_result")
    assert not reviews


def test_triage_exception_does_not_fall_through_into_team_dispatch(emitter, monkeypatch):
    output, reviews = emitter
    def broken(*args, **kwargs):
        raise RuntimeError("Synthetic inline exception")
    monkeypatch.setattr(front_door, "manager_triage", broken)
    result = dispatch(output, route="complex")
    assert result is not None and result["kind"] == "error"
    assert not reviews


def test_normal_chat_is_not_an_error_even_when_it_discusses_errors(emitter, monkeypatch):
    output, reviews = emitter
    reply = "This documentation discusses request failures and quota limits."
    monkeypatch.setattr(front_door, "manager_triage", lambda *a, **k: reply)
    result = dispatch(output, {"_self_failure": {"detail": "stale prior failure"}})
    assert result == {"kind": "chat", "reply": reply}
    assert reviews == [reply]
