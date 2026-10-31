"""s-a3f1c15b: incompatible action fields must never become successful work."""
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError, validate

from argus.core.handoff import HandOffActions
from argus.core.models import RunnerOptions
from argus.core.role_actions import role_actions
from argus.manager.form import manager_actions


def test_model_schema_separates_question_from_ticket_and_management():
    actions = manager_actions("write a PDF", active_mission=False, operator_available=True)
    schema = actions.tools[0]["inputSchema"]
    question = {"to": "operator", "summary": "Choose scope.", "assumptions": [],
                "ask": {"question": "Which scope?", "options": []}}
    ticket = {"to": "team", "summary": "Write it.", "assumptions": [], "goal": "PDF",
              "acceptance": ["Compiled readable PDF"], "vertical": "research"}
    for payload in (question, ticket):
        validate({"decision": payload}, schema)
        actions.dispatch("hand_off", {"decision": payload})
        assert actions.handoff.to == payload["to"]
    for payload in ({**question, "answer": {"item": "scope", "decision": "pending"}},
                    {**ticket, "ask": question["ask"]}):
        with pytest.raises(ValidationError):
            validate({"decision": payload}, schema)
        with pytest.raises(ValueError):
            actions.dispatch("hand_off", payload)
        assert actions.handoff is None and actions.last_error


def test_optional_null_is_absent_but_nonempty_conflicting_intent_is_not_discarded():
    actions = manager_actions("write a PDF", active_mission=False, operator_available=True)
    payload = {"to": "operator", "summary": "Choose scope.", "assumptions": [],
               "ask": {"question": "Which scope?", "options": [
                   {"label": "Hardware", "description": None}, {"label": "Software"}]},
               "answer": None, "control": None}
    actions.dispatch("hand_off", payload)
    assert "answer" not in actions.handoff.payload
    assert "description" not in actions.handoff.payload["ask"]["options"][0]
    with pytest.raises(ValueError, match="to=manager"):
        actions.dispatch("hand_off", {**payload, "answer": {"item": "scope", "decision": "pending"}})
    with pytest.raises(ValueError):
        actions.dispatch("hand_off", {**payload, "summary": None})


def test_repeated_refusals_interrupt_but_successful_correction_clears_failure():
    actions = manager_actions("go", active_mission=False, operator_available=True)
    invalid = {"to": "team", "summary": "Go.", "assumptions": [],
               "ask": {"question": "Ready?", "options": []}}
    for i in range(3):
        with pytest.raises(ValueError):
            actions.dispatch("hand_off", invalid)
        assert bool(actions.interrupt_reason()) == (i == 2)
    actions.dispatch("hand_off", {"to": "manager", "summary": "No change.", "assumptions": []})
    assert not actions.last_error and actions.interrupt_reason() is None


def test_bridge_keeps_outer_cancellation_and_checks_action_failure():
    actions = HandOffActions("manager")
    outer = [None]
    options = RunnerOptions(external_interrupt_reason_provider=lambda: outer[0])
    with role_actions(SimpleNamespace(backend="pi"), options, actions) as bound:
        assert bound.external_interrupt_reason_provider() is None
        actions.consecutive_refusals = 3
        actions.last_error = "conflicting fields"
        assert "conflicting fields" in bound.external_interrupt_reason_provider()
        outer[0] = "user cancelled"
        assert bound.external_interrupt_reason_provider() == "user cancelled"


@pytest.mark.parametrize("frozen", [False, True])
def test_dsh_bridge_uses_private_patch_and_supported_mcp_entrypoint(monkeypatch, frozen):
    import json
    import sys
    from pathlib import Path

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    with role_actions(SimpleNamespace(backend="dsh"), RunnerOptions(), HandOffActions("manager")) as bound:
        patch = Path(bound.extra_args[-1])
        config = json.loads(patch.read_text(encoding="utf-8"))[0]["insert"][0]["config"]
        assert ("-P" not in config["args"]) == frozen
        assert config["args"][-2:] == ["-m", "argus.core.role_actions"]
        assert config["failOnStartupError"] is True
        assert config["reconnect"] == {"enabled": False}
        assert "mcp__argus_actions__hand_off" in bound.trusted_tool_names
        assert not any(value in " ".join(bound.extra_args) for value in config["env"].values())
    assert not patch.exists()
