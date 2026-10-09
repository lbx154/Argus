"""When the Manager is asked to supervise, and how its reply is read.

Supervision is consulted where a decision is pending: a stalled or repeating
round, a blocked or unclear review, an operator question, a nearly spent round
budget, a periodic checkpoint, or work that remains after a mission. A clear
review that made progress already reaches the Engineer, and a reviewed success
with nothing left has no course to steer, so neither starts a model call.
"""
from __future__ import annotations

import json

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.daemon.state import write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager, supervision


class _Backend:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.prompts: list[str] = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.prompts.append(prompt)
        return RunnerResult(exit_code=0, call_id="offline", agent_messages=[self.text])


@pytest.fixture()
def admitted(tmp_path, monkeypatch):
    """Schedule one event and report whether it was admitted, without running it."""
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    item = BacklogItem.new(item_id="grouped", title="Validate grouped means", objective="Keep missing-value checks")
    Backlog(tmp_path / "backlog.jsonl").add(item)
    monkeypatch.setattr(supervision, "_dispatch_pending", lambda: None)
    manager = Manager(tmp_path, runner=_Backend(), memory_maintenance_enabled=False)
    supervision.start_supervision(tmp_path)

    def schedule(event: dict) -> dict | None:
        supervision._PENDING.pop(str(tmp_path.resolve()), None)
        if not supervision.schedule_supervision(manager, tmp_path, event):
            return None
        return supervision._PENDING.pop(str(tmp_path.resolve()))[2]

    yield schedule
    supervision.shutdown_supervision(tmp_path)


def _review(round_index: int, **extra) -> dict:
    return {
        "type": EventType.ROUND_REVIEW_COMPLETED, "item_id": "grouped", "status": "continue",
        "reason": "The grouped mean is wrong for group B.", "round_index": round_index, **extra,
    }


def test_a_clear_first_review_reaches_the_engineer_without_a_manager_call(admitted):
    assert admitted(_review(1, forward_progress=False)) is None
    assert admitted(_review(1, forward_progress=True)) is None


@pytest.mark.parametrize("progress", [False, None, "false"])
def test_a_later_round_without_forward_progress_asks_the_manager(admitted, progress):
    extra = {} if progress is None else {"forward_progress": progress}
    event = admitted(_review(2, **extra))
    assert event is not None
    assert event["consult_reason"] == "no forward progress after a correction round"


def test_progressing_rounds_still_get_a_periodic_checkpoint(admitted):
    assert admitted(_review(2, forward_progress=True)) is None
    event = admitted(_review(3, forward_progress=True))
    assert event is not None and event["consult_reason"] == "periodic checkpoint"


def test_the_checkpoint_counts_from_the_last_consulted_round(admitted, tmp_path):
    supervision._write(tmp_path / "manager-supervision" / "latest.json", {
        "status": "applied", "trigger": {"type": EventType.ROUND_REVIEW_COMPLETED, "item_id": "grouped", "round_index": 2},
    })
    assert admitted(_review(3, forward_progress=True)) is None
    assert admitted(_review(4, forward_progress=True)) is None
    assert admitted(_review(5, forward_progress=True))["consult_reason"] == "periodic checkpoint"


@pytest.mark.parametrize(("extra", "reason"), [
    ({"status": "blocked"}, "blocked review"),
    ({"operator_question": "Which column holds the group?"}, "operator question"),
    ({"review_skipped": True}, "review verdict unavailable"),
    ({"backend_unavailable": True}, "review verdict unavailable"),
    ({"round_index": 0}, "review round unknown"),
    ({"round_max": 2}, "round budget nearly spent"),
])
def test_review_signals_that_leave_a_decision_open_ask_the_manager(admitted, extra, reason):
    event = admitted({**_review(1, forward_progress=True), **extra})
    assert event is not None and event["consult_reason"] == reason


def test_a_task_waiting_for_the_operator_asks_the_manager(admitted, tmp_path):
    Backlog(tmp_path / "backlog.jsonl").update("grouped", pending_question="Which column holds the group?")
    assert admitted(_review(1, forward_progress=True))["consult_reason"] == "operator question"


def test_an_undelivered_decision_is_still_delivered_on_the_next_review(admitted, tmp_path):
    supervision._write(tmp_path / "manager-supervision" / "latest.json", {"status": "issued"})
    assert admitted(_review(1, forward_progress=True))["consult_reason"] == "issued decision awaiting delivery"


def test_a_project_done_verdict_with_nothing_left_has_no_course_to_steer(admitted, tmp_path):
    Backlog(tmp_path / "backlog.jsonl").update("grouped", status="done")
    done = {"type": EventType.LIFE_PLANNER_VERDICT, "status": "completed", "project_done": True}
    assert admitted(done) is None
    assert admitted({"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "grouped", "success": True, "status": "done"}) is None
    # A verdict that plans more work, or a failed mission, is still supervised.
    assert admitted({"type": EventType.LIFE_PLANNER_VERDICT, "status": "planned", "project_done": False}) is not None
    assert admitted({"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "grouped", "success": False, "status": "error"}) is not None


def test_the_consult_reason_reaches_the_prompt_and_the_receipt(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend(json.dumps({"action": "continue", "reason": "The repair is under way.", "evidence_refs": ["backlog.jsonl"]}))
    event = {**_review(2, forward_progress=False), "consult_reason": "no forward progress after a correction round"}
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "applied"
    assert "consulted now because of: no forward progress" in backend.prompts[0]
    assert "ACTION: continue, steer or wait" in backend.prompts[0]
    assert record["trigger"]["round_index"] == 2
    assert record["trigger"]["consult_reason"] == "no forward progress after a correction round"


# --------------------------------------------------------------------------- reading replies

REFS = "handoffs/a/round-0001.json; handoffs/a/frontier.json"


@pytest.mark.parametrize(("text", "action"), [
    # Every field on one line, the verdict under DECISION and prose under ACTION.
    (f"DECISION: STEER — ACTION: Obtain reproducible test evidence. REASON: The pass claim is unverified. "
     f"EVIDENCE_REFS: {REFS}\nDIRECTIVE: Run the regression tests and report the command.", "steer"),
    # ACTION prose that starts with another verb does not outvote DECISION.
    (f"DECISION: STEER — ACTION: Continue independent review; REASON: The score is unread. "
     f"EVIDENCE_REFS: {REFS} — DIRECTIVE: Render the score and compare it.", "steer"),
    (f"DECISION: CONTINUE — ACTION: Accept the completed task. REASON: Review passed. EVIDENCE_REFS: {REFS}", "continue"),
    # The verb leads the ACTION value and prose follows it.
    (f"ACTION: STEER — run the fixture through the real CLI.\nREASON: The header is unproven.\n"
     f"EVIDENCE_REFS: {REFS}\nDIRECTIVE: Run the fixture.", "steer"),
    # Each key alone on its line with the value below it.
    (f"ACTION\nCONTINUE\n\nREASON\nThe reviewer certified the checklist.\n\nEVIDENCE_REFS\n{REFS}", "continue"),
])
def test_replies_in_the_shapes_models_actually_wrote_are_read(text, action):
    decision = supervision._decision(text)
    assert decision["action"] == action
    assert decision["reason"]
    assert decision["cited_refs"] == ["handoffs/a/round-0001.json", "handoffs/a/frontier.json"]
    if action == "steer":
        assert decision["directive"]


@pytest.mark.parametrize("text", [
    "ACTION: continue-to-elsewhere\nREASON: odd\nEVIDENCE_REFS: backlog.jsonl",
    "The action: continue was considered. Reason: unclear.",
    "DECISION: maybe — REASON: unsure. EVIDENCE_REFS: backlog.jsonl",
])
def test_rescue_reading_never_invents_a_decision(text):
    with pytest.raises(supervision.SupervisionDecisionError):
        supervision._decision(text)
