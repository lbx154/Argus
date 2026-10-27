"""A plan made before the operator's newest instruction does not start.

Live trial s-efe9deac (2026-10-26): a Planner call started at 01:28:54; at
01:31:26 the person wrote "stop re-picking the topic, pick one of the checked
routes and go to experiments"; at 01:32:08 the call committed, and started, a
new topic that contradicted it. The operator context revision moved during the
call, so that plan must not start and the Planner replans with the instruction.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.operator_context import OperatorContextStore, append_directive
from argus.life.supervisor._constants import PLAN_RETRY
from tests.hand_off import plan
from tests.test_liveness_lifecycle import _PlannerBackend, _project, _supervisor

_NEW_TOPIC = plan(summary="Replace the rejected route with a new topic.", tasks=[
    {"title": "Form a new research topic", "objective": "Find a different route."},
])
_PICK_ROUTE = plan(summary="Pick the checked route and run its first experiment.", tasks=[
    {"title": "Run the first experiment on the checked route", "objective": "Experiment."},
])
_INSTRUCTION = "不要再反复否决选题了，从核查过的路线里选一条，进入实验阶段。"


class _InstructionArrivesMidCall(_PlannerBackend):
    """The person writes while the first call is still running."""

    def __init__(self, life: Path, replies: list) -> None:
        super().__init__(replies)
        self.life = life

    def run_exec(self, **kwargs):  # noqa: ANN003
        if not self.calls:
            append_directive(
                self.life, _INSTRUCTION,
                scope="project", applies_to_roles=("planner",), lifetime="once", source="operator.message",
                expected_revision=OperatorContextStore(self.life).revision,
            )
        return super().run_exec(**kwargs)


def test_a_plan_committed_after_a_newer_instruction_is_not_started(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "life"
    planner = _InstructionArrivesMidCall(life, [_NEW_TOPIC, _PICK_ROUTE])
    supervisor = _supervisor(project, life, planner)

    assert supervisor._plan_next_work() == PLAN_RETRY
    # Nothing the stale call planned exists, so nothing of it can start.
    assert supervisor.memory.backlog.active() == []
    events = [json.loads(line) for line in (supervisor.memory.root / "events.jsonl").read_text().splitlines()]
    [dropped] = [row for row in events if row.get("superseded_by_operator")]
    assert dropped["dropped_tasks"] == ["Form a new research topic"]
    assert dropped["operator_context_revision"] > dropped["seen_operator_context_revision"]

    # The Planner is asked again and now sees the instruction.
    assert supervisor._plan_next_work() is True
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Run the first experiment on the checked route",
    ]
    assert _INSTRUCTION in planner.calls[1]["prompt"]


def test_a_plan_with_no_newer_instruction_starts(tmp_path: Path, monkeypatch) -> None:
    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([_PICK_ROUTE])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Run the first experiment on the checked route",
    ]



def test_words_written_before_the_call_leave_the_plan_to_start(tmp_path: Path, monkeypatch) -> None:
    from argus.core.operator_context import operator_context_state_root
    from argus.core.transcript import append_turn

    project = _project(tmp_path, monkeypatch)
    planner = _PlannerBackend([_PICK_ROUTE])
    supervisor = _supervisor(project, tmp_path / "life", planner)
    root = operator_context_state_root(supervisor.memory)
    # Heard before the call began: the plan was made with it.
    assert append_turn(root, "operator", _INSTRUCTION)

    assert supervisor._plan_next_work() is True
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Run the first experiment on the checked route",
    ]

def test_a_message_sent_to_the_manager_during_the_call_stops_the_plan(tmp_path: Path, monkeypatch) -> None:
    """The live order of s-efe9deac: the person writes to the Manager while the
    Planner call runs, and the Manager has handed the team nothing yet when the
    plan commits. No lock is held anywhere; the conversation journal is the signal.
    """
    from argus.manager import front_door
    from argus.webapi import manager_bridge, manager_state
    from tests.hand_off import manager_runner

    sid = "s-efe9deac"
    project = _project(tmp_path, monkeypatch)
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    manager_state._STATES.clear()
    monkeypatch.setattr(
        front_door, "_ensure_manager_runner",
        lambda *_a: manager_runner(hand=None, reply="好的，我会转告团队。"),
    )

    class _PersonWritesMidCall(_PlannerBackend):
        def run_exec(self, **kwargs):  # noqa: ANN003
            if not self.calls:
                result = manager_bridge.manager_message(sid, _INSTRUCTION, global_root=tmp_path)
                assert result["kind"] == "chat"
            return super().run_exec(**kwargs)

    planner = _PersonWritesMidCall([_NEW_TOPIC, _PICK_ROUTE])
    supervisor = _supervisor(project, life, planner)
    # One unreadable row in the journal must not blind the check to the rest.
    with (life / "transcript.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "not-a-time", "role": "operator", "text": "garbled"}) + "\n")
    seen = OperatorContextStore(life).revision

    assert supervisor._plan_next_work() == PLAN_RETRY
    assert supervisor.memory.backlog.active() == []
    assert OperatorContextStore(life).revision > seen

    assert supervisor._plan_next_work() is True
    assert [item.title for item in supervisor.memory.backlog.pending()] == [
        "Run the first experiment on the checked route",
    ]
    assert _INSTRUCTION in planner.calls[1]["prompt"]
