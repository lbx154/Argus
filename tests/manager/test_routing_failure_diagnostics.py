from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.manager import Manager
from argus_skill.manager.domain_author import (
    ManagerClassificationContractError,
    VerticalDecisionError,
)
from argus_skill.manager.front_door import PreparedManagerHandoff


class _Result:
    def __init__(self, message: str = "", *, fatal_error: str = "") -> None:
        self.last_agent_message = message
        self.agent_messages = [message]
        self.tool_activity_observed = True
        self.fatal_error = fatal_error
        self.exit_code = 1 if fatal_error else 0


class _Runner:
    def __init__(self, result: object) -> None:
        self.result = result

    def run_exec(self, **_kwargs):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.mark.parametrize("phase", ["backend", "parse", "contract", "timeout"])
def test_vertical_decision_error_string_leads_with_phase_and_bounds_task(
    phase: str,
) -> None:
    error = VerticalDecisionError(
        "401 Missing bearer",
        phase=phase,
        attempts=2,
        task="x" * 200,
    )

    assert str(error).startswith(f"routing failed [{phase}]: 401 Missing bearer")
    assert error.task_excerpt.endswith("…")
    assert len(error.task_excerpt) == 80


def test_backend_failure_preserves_provider_cause_and_event_fields(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    runner = _Runner(_Result(fatal_error="401 Missing bearer"))
    manager = Manager(
        project_root=tmp_path,
        execution_workdir=tmp_path,
        runner=runner,
        memory_maintenance_enabled=False,
    )

    with pytest.raises(VerticalDecisionError) as caught:
        manager.decide_vertical("Route this task without echoing all of it.")

    assert caught.value.phase == "backend"
    assert caught.value.cause == "401 Missing bearer"
    assert caught.value.backend_error == "401 Missing bearer"
    assert caught.value.attempts == 1

    prepared = PreparedManagerHandoff(
        mem=SimpleNamespace(project_root=tmp_path),
        body="Route this task without echoing all of it.",
        manager=None,
        decision=None,
        intent_id="intent-test",
        root_task_id="item-test",
    )
    prepared.failed(caught.value)
    event = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[-1])
    assert event["phase"] == "backend"
    assert event["cause"] == "401 Missing bearer"
    assert event["backend_error"] == "401 Missing bearer"
    assert event["attempts"] == 1


def test_contract_failure_names_field_and_bounds_reply_snippet(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    reply = json.dumps({
        "choice": "existing",
        "vertical": "research",
        "workflow_mode": "staged",
        "research_target_level": "phd",
        "execution_task": "Investigate the claim.",
        "rationale": "research request",
        "padding": "z" * 500,
    })
    manager = Manager(
        project_root=tmp_path,
        execution_workdir=tmp_path,
        runner=_Runner(_Result(reply)),
        memory_maintenance_enabled=False,
    )

    with pytest.raises(ManagerClassificationContractError) as caught:
        manager.decide_vertical("Investigate the claim.")

    assert caught.value.phase == "contract"
    assert caught.value.contract_field == "research_target_level"
    assert 'got "phd"' in caught.value.cause
    assert "exploratory|publishable|doctoral" in caught.value.cause
    assert 0 < len(caught.value.model_reply_snippet) <= 300
    assert caught.value.attempts == 2
