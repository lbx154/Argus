"""Routing context must not become the public lifecycle objective."""
from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.core.mission_view import load_mission_view, update_mission_view_event
from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.daemon.state import read_continuous_state, write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager, config_intent, front_door
from argus.manager.domain_author import VerticalDecision
from argus.webapi import manager_bridge, manager_state

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


@pytest.mark.parametrize("outcome", ["success", "decision-failure", "commit-failure"])
@pytest.mark.parametrize("public_goal", [NEW_GOAL, "Explain the literal [CURRENT OPERATOR MESSAGE] delimiter."])
def test_real_message_keeps_routing_context_private_through_intent_events(tmp_path, monkeypatch, outcome, public_goal):
    sid = "s-public-objective"
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(life), workdir=str(life), objective=OLD_GOAL))
    write_continuous_config(life, enabled=True, objective=OLD_GOAL)
    Backlog(life / "backlog.jsonl").add(BacklogItem.new(title="Prior task", objective=OLD_GOAL))
    append_turn(life, "operator", OLD_GOAL)
    manager_state._STATES.pop(sid, None)
    manager = Manager(life, memory_maintenance_enabled=False)
    model_inputs, commit_inputs = [], []

    def classify(_mem, _body, state, **_kwargs):
        state["_frontdoor_lifetime"] = "standing"
        return None, None, "complex"

    def decide(body, **_kwargs):
        model_inputs.append(body)
        assert OLD_GOAL in body and public_goal in body
        assert "[BOUNDED TASK CONTEXT" in body and "[CURRENT OPERATOR MESSAGE]" in body
        # The real started event is already public while inference is pending.
        assert intent_events(life)[-1]["objective"] == public_goal
        assert load_mission_view(life)["mission"]["objective"] == public_goal
        if outcome == "decision-failure":
            raise RuntimeError("Synthetic decision failure")
        return VerticalDecision(choice="existing", vertical="research", workflow_mode="direct",
                                execution_task=" \n" + EXECUTION + "\n ", research_target_level="exploratory")

    real_commit = manager.commit_vertical_decision

    def commit(body, decision, **kwargs):
        commit_inputs.append(body)
        if outcome == "commit-failure":
            raise RuntimeError("Synthetic commit failure")
        return real_commit(body, decision, **kwargs)

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(front_door, "_ensure_manager_runner", lambda *_args: SimpleNamespace(manager=manager))
    monkeypatch.setattr(manager, "decide_vertical", decide)
    monkeypatch.setattr(manager, "commit_vertical_decision", commit)
    result = manager_bridge.manager_message(sid, public_goal, global_root=tmp_path)
    events = intent_events(life)
    assert len(model_inputs) == 1
    assert events[0]["type"] == "life.manager.intent.started" and events[0]["objective"] == public_goal
    if outcome == "success":
        completed = next(row for row in events if row["type"] == "life.manager.intent.completed")
        assert result["kind"] == "task"
        assert completed["objective"] == completed["execution_task"] == EXECUTION
        assert commit_inputs == model_inputs
        assert read_continuous_state(life).objective == EXECUTION
        pending = Backlog(life / "backlog.jsonl").pending()
        assert len(pending) == 1 and pending[0].objective == EXECUTION
        view = load_mission_view(life)
        assert view["mission"]["objective"] == EXECUTION
        assert "[BOUNDED TASK CONTEXT" not in json.dumps(view)
    else:
        failed = next(row for row in events if row["type"] == "life.manager.intent.failed")
        assert failed["objective"] == public_goal
        assert not any(row["type"] == "life.manager.intent.completed" for row in events)
        assert read_continuous_state(life).objective == OLD_GOAL
        assert [item.objective for item in Backlog(life / "backlog.jsonl").pending()] == [OLD_GOAL]
    # A user may legitimately discuss a delimiter; no string filter strips it.
    assert all(row["objective"] in {public_goal, EXECUTION} for row in events)
    assert all(OLD_GOAL not in row["objective"] for row in events)


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
