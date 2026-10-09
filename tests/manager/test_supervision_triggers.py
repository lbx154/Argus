"""When the Manager is asked to supervise, and how its reply is read.

The Reviewer, which already judges each round, says whether the course needs
the Manager; its other structured signals count as asking, and without an
answer the Manager looks. Safety nets the Reviewer cannot see still fire: an
operator question, an unusable verdict, an undelivered decision, a run of
no-progress verdicts. A bounded run that is ending has no course to steer.
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


def _not_needed(**extra) -> dict:
    return {"manager_attention": "not_needed", "manager_attention_reason": "Clear repair under way.", **extra}


def test_the_reviewer_s_judgment_decides_whether_the_manager_looks(admitted):
    assert admitted(_review(1, **_not_needed())) is None
    asked = admitted(_review(1, manager_attention="needed", manager_attention_reason="The same fix keeps failing."))
    assert asked["consult_reason"] == "reviewer asks for the Manager: The same fix keeps failing."


@pytest.mark.parametrize("round_index", [1, 2, 5])
def test_without_the_reviewer_s_answer_the_manager_looks(admitted, round_index):
    event = admitted(_review(round_index, forward_progress=True))
    assert event["consult_reason"] == "reviewer did not say whether the Manager is needed"


@pytest.mark.parametrize(("extra", "reason"), [
    ({"plan_signal": "reconsider"}, "reviewer plan signal: reconsider"),
    ({"plan_challenge": "The parser is the wrong layer."}, "reviewer challenges the plan"),
    ({"authority_impact": "manager_contract"}, "decision beyond the engineer's authority"),
    ({"authority_impact": "operator"}, "decision beyond the engineer's authority"),
    ({"checkpoint_recommended": True}, "reviewer recommends a checkpoint"),
    ({"session_signal": {"kind": "repeated_contradiction", "target": "engineer", "detail": "x"}}, "reviewer session signal"),
    ({"verification_obstacle": "The display masks the header value."}, "evidence the reviewer cannot observe"),
])
def test_the_reviewer_s_structured_signals_count_as_asking(admitted, extra, reason):
    event = admitted(_review(1, **_not_needed(**extra)))
    assert event is not None and event["consult_reason"] == reason


@pytest.mark.parametrize(("extra", "reason"), [
    ({"status": "blocked"}, "blocked review"),
    ({"operator_question": "Which column holds the group?"}, "operator question"),
    ({"review_skipped": True}, "review verdict unavailable"),
    ({"backend_unavailable": True}, "review verdict unavailable"),
])
def test_safety_nets_fire_whatever_the_reviewer_said(admitted, extra, reason):
    event = admitted({**_review(1, **_not_needed()), **extra})
    assert event is not None and event["consult_reason"] == reason


def test_a_task_waiting_for_the_operator_reaches_the_manager(admitted, tmp_path):
    Backlog(tmp_path / "backlog.jsonl").update("grouped", pending_question="Which column holds the group?")
    assert admitted(_review(1, **_not_needed()))["consult_reason"] == "operator question pending"


def test_an_undelivered_decision_is_still_delivered_on_the_next_review(admitted, tmp_path):
    supervision._write(tmp_path / "manager-supervision" / "latest.json", {"status": "issued"})
    assert admitted(_review(1, **_not_needed()))["consult_reason"] == "issued decision awaiting delivery"


def test_a_no_progress_streak_reaches_the_manager_even_when_not_asked(admitted):
    stall = {"type": EventType.ROUND_STALL, "round_index": 2, "semantic_stall_streak": 1}
    assert admitted(stall) is None
    event = admitted({**stall, "round_index": 3, "semantic_stall_streak": 2})
    assert event["consult_reason"] == "2 rounds without forward progress"


def test_a_failed_check_does_not_count_as_the_manager_having_looked(tmp_path):
    """The next trigger on the same evidence asks again after a failed or superseded check."""
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    manager = Manager(tmp_path, runner=_Backend("I am not sure."), memory_maintenance_enabled=False)
    event = {**_review(2), "consult_reason": "2 rounds without forward progress"}
    assert supervision.supervise(manager, tmp_path, event)["status"] == "failed"
    good = json.dumps({"action": "steer", "reason": "The same failure repeats; change approach.",
                       "directive": "Write a reproducible check first.", "evidence_refs": ["backlog.jsonl"]})
    manager.runner.text = good
    assert supervision.supervise(manager, tmp_path, event)["status"] == "applied"
    assert len(manager.runner.prompts) == 2


def _final_review(tmp_path, attention: str | None) -> None:
    from argus.life.event_log import JsonlEventSink

    review = {"type": EventType.ROUND_REVIEW_COMPLETED, "item_id": "grouped", "status": "done",
              "reason": "Accepted.", "round_index": 1}
    if attention:
        review["manager_attention"] = attention
    JsonlEventSink(None, life_dir=tmp_path).append(review)


SUCCESS = {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "grouped", "success": True, "status": "done"}
DONE = {"type": EventType.LIFE_PLANNER_VERDICT, "status": "completed", "project_done": True}


@pytest.mark.parametrize(("attention", "asked"), [("not_needed", False), ("needed", True), (None, True)])
def test_continuous_success_follows_the_final_review_s_judgment(admitted, tmp_path, attention, asked):
    Backlog(tmp_path / "backlog.jsonl").update("grouped", status="done")
    _final_review(tmp_path, attention)
    assert (admitted(SUCCESS) is not None) is asked
    assert (admitted(DONE) is not None) is asked


def test_a_bounded_run_that_is_ending_has_no_course_to_steer(admitted, tmp_path):
    Backlog(tmp_path / "backlog.jsonl").update("grouped", status="done")
    write_continuous_config(tmp_path, enabled=False, objective="Produce a validated grouped summary", open_ended=False)
    _final_review(tmp_path, "needed")
    assert admitted(SUCCESS) is None
    assert admitted(DONE) is None
    supervision._write(tmp_path / "manager-supervision" / "latest.json", {"status": "issued"})
    assert admitted(SUCCESS)["consult_reason"] == "issued decision awaiting delivery"


def test_work_left_or_a_failure_after_a_mission_is_still_supervised(admitted, tmp_path):
    _final_review(tmp_path, "not_needed")
    assert admitted(SUCCESS)["consult_reason"] == "work remains after the mission"
    assert admitted({**SUCCESS, "success": False, "status": "error"}) is not None
    assert admitted({"type": EventType.LIFE_PLANNER_VERDICT, "status": "planned", "project_done": False}) is not None


def test_the_consult_reason_reaches_the_prompt_and_the_receipt(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend(json.dumps({"action": "continue", "reason": "The repair is under way.", "evidence_refs": ["backlog.jsonl"]}))
    event = {**_review(2, forward_progress=False), "consult_reason": "reviewer asks for the Manager"}
    record = supervision.supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "applied"
    assert "consulted now because of: reviewer asks for the Manager" in backend.prompts[0]
    assert "ACTION: continue, steer or wait" in backend.prompts[0]
    assert record["trigger"]["round_index"] == 2
    assert record["trigger"]["consult_reason"] == "reviewer asks for the Manager"


# --------------------------------------------------------------------------- reading replies

REFS = "handoffs/a/round-0001.json; handoffs/a/frontier.json"


@pytest.mark.parametrize(("text", "action"), [
    # Every field on one line, the verdict under DECISION and prose under ACTION.
    (f"DECISION: STEER — ACTION: Obtain reproducible test evidence. REASON: The pass claim is unverified. "
     f"EVIDENCE_REFS: {REFS}\nDIRECTIVE: Run the regression tests and report the command.", "steer"),
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


R = "EVIDENCE_REFS: backlog.jsonl"


@pytest.mark.parametrize("text", [
    "ACTION: continue-to-elsewhere\nREASON: odd\nEVIDENCE_REFS: backlog.jsonl",
    "The action: continue was considered. Reason: unclear.",
    "DECISION: maybe — REASON: unsure. EVIDENCE_REFS: backlog.jsonl",
    # A verb followed by a negation, a hedge or a question is not a decision.
    f"ACTION: Steer is not needed; continue the current course.\nREASON: progress is real.\n{R}\nDIRECTIVE: none",
    f"ACTION: STEER not warranted; CONTINUE\nREASON: fine\n{R}",
    f"DECISION: I would not STEER here. REASON: progress ok. {R}",
    f"ACTION: wait and see whether the reviewer accepts\nREASON: the engineer fixed it\n{R}",
    f"ACTION: Continue, but STEER if round 4 repeats\nREASON: ok\n{R}\nDIRECTIVE: Repeat the fixture.",
    f"Option A — DECISION: STEER, DIRECTIVE: rerun tests. On reflection that is unnecessary. REASON: verified. {R}",
    f"DECISION: Steer? No — continuing is justified. REASON: verified. {R}",
    f"DECISION: STEER — not needed. REASON: verified. {R}",
    # A DECISION verb and a different verb opening ACTION conflict.
    f"DECISION: CONTINUE — ACTION: STEER the engineer to rerun. REASON: x. {R} DIRECTIVE: rerun.",
    f"DECISION: STEER — ACTION: Continue independent review; REASON: unread. {R} — DIRECTIVE: Render it.",
    f"My Decision: steer. Reason: unclear. {R}",
])
def test_rescue_reading_never_invents_a_decision(text):
    with pytest.raises(supervision.SupervisionDecisionError):
        supervision._decision(text)
