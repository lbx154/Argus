"""Routing context must not become the public lifecycle objective."""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.mission_view import load_mission_view, update_mission_view_event
from argus.manager import front_door

OLD_GOAL = "OLD_CONTEXT_ONLY: collect every batch size before comparing groups."
NEW_GOAL = "Compare the two groups at the same batch size."
EXECUTION = "Compare both groups at the same batch size and retain the missing-value exclusions."


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Public-objective regressions cannot call a provider")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)


def intent_events(root):
    return [row for line in (root / "events.jsonl").read_text().splitlines()
            if (row := json.loads(line)).get("type", "").startswith("life.manager.intent.")]


def test_completed_event_uses_the_validated_committed_execution_task(tmp_path):
    prepared = front_door.PreparedManagerHandoff(
        mem=SimpleNamespace(project_root=tmp_path), body="Private model routing context",
        manager=None, decision=SimpleNamespace(execution_task="Earlier draft"),
        intent_id="intent-normalized", root_task_id="task-normalized", public_objective=NEW_GOAL,
    )
    prepared.completed(SimpleNamespace(execution_task="  " + EXECUTION + "\n", workflow_mode="direct"))
    event = intent_events(tmp_path)[0]
    assert event["objective"] == event["execution_task"] == EXECUTION
    assert prepared.body == "Private model routing context"


@pytest.mark.parametrize("execution", [EXECUTION, None, "", "  "])
def test_python_projection_prefers_legacy_execution_task_with_objective_fallback(tmp_path, execution):
    objective = "[CURRENT OPERATOR MESSAGE] is a delimiter the user wants explained."
    event = {"type": "life.manager.intent.completed", "ts": 1, "item_id": "legacy-task",
             "objective": objective, "execution_task": execution}
    view = update_mission_view_event(tmp_path, event)
    expected = EXECUTION if execution == EXECUTION else objective
    assert view["mission"]["objective"] == expected
    assert view["mission"]["title"] == expected
    assert load_mission_view(tmp_path)["mission"]["objective"] == expected
