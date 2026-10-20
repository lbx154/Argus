"""A Manager STOP parks the stuck run it was decided on; nothing else.

With no round ceiling and no guard that ends a supervised mission, the Manager
is how genuinely stuck work ends: STOP parks the run at its next role boundary
where the Planner can retire it or the operator resume it, workspace intact.
"""
from __future__ import annotations

import json

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.core.stop_kinds import park_reason_of, park_rule
from argus.daemon.state import write_continuous_config
from argus.engineer.round_config import SupervisedConfig
from argus.engineer.round_manager_wait import manager_wait_terminal
from argus.engineer.round_state import RoundLoopState
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager, supervision

REASON = "Eleven rounds rewrote the same parser and the residual never moved."


class _Backend:
    def __init__(self, text: str) -> None:
        self.text = text

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        return RunnerResult(exit_code=0, call_id="offline", agent_messages=[self.text])


def test_stop_is_a_typed_action():
    decision = supervision._decision(f"ACTION: stop\nREASON: {REASON}\nEVIDENCE_REFS: backlog.jsonl")
    assert decision["action"] == "stop"


def test_a_manager_stop_parks_the_run_it_was_decided_on(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    backlog = Backlog(tmp_path / "backlog.jsonl")
    backlog.add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backlog.mark_running("grouped")
    answer = json.dumps({"action": "stop", "reason": REASON, "evidence_refs": ["backlog.jsonl"]})
    manager = Manager(tmp_path, runner=_Backend(answer), memory_maintenance_enabled=False)
    stall = {"type": EventType.ROUND_STALL, "item_id": "grouped", "round_index": 11,
             "consult_reason": "Reviewer reported no forward progress for 11 consecutive rounds."}

    record = supervision.supervise(manager, tmp_path, stall)

    assert record["status"] == "applied" and record["effects"]["effect"] == "mission parked"
    config = SupervisedConfig(operator_question_policy_root=tmp_path, session_id="grouped")
    status, _rounds, _message, reason, _thread = manager_wait_terminal(config, RoundLoopState())
    assert (status, reason) == ("paused_no_breakthrough", f"Manager decision: {REASON}")
    assert park_rule(park_reason_of(status)).holder == "planner"
    # Resuming starts a new run, which the old decision does not stop.
    backlog.update("grouped", status=status)
    backlog.resume_paused("grouped")
    backlog.mark_running("grouped")
    assert manager_wait_terminal(config, RoundLoopState()) is None
