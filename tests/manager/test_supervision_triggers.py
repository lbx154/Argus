"""When the Manager is asked to supervise, and how its reply is read.

Only what is put to it: an Engineer's ``→ manager``, a check whose risk the
Manager accepts in the operator's place, a workspace the Reviewer changed while
judging, a stalled Engineer turn, an Observer remark, a runtime incident. Nothing counts rounds or watches the Reviewer's
mood; a blocked verdict goes to the stage decision, a question to the
operator, and any other boundary only delivers a decision already issued.
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
    manager = Manager(tmp_path, runner=_Backend())
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


@pytest.mark.parametrize("extra", [
    {},
    {"status": "blocked"},
    {"status": "blocked", "operator_question": "Which column holds the group?"},
    {"review_skipped": True},
    {"plan_signal": "reconsider", "plan_challenge": "The parser is the wrong layer."},
    {"checkpoint_recommended": True},
    {"session_signal": {"kind": "repeated_contradiction", "target": "engineer", "detail": "x"}},
    # A field written before this rule is read and ignored.
    {"manager_attention": "needed", "manager_attention_reason": "Worth a look."},
])
def test_a_review_puts_nothing_to_the_manager_by_itself(admitted, extra):
    for round_index in range(1, 7):
        assert admitted(_review(round_index, **extra)) is None


def test_an_engineer_handing_the_work_over_reaches_the_manager(admitted):
    event = admitted(_review(2, status="blocked", review_source="engineer_handoff",
                             reason="The fixture needs a credential only you can grant."))
    assert event["consult_reason"] == (
        "the Engineer handed the work to you: The fixture needs a credential only you can grant."
    )


def test_an_impossible_check_reaches_the_manager_only_in_the_operator_s_place(admitted, monkeypatch):
    review = _review(1, verification_obstacle="The token exists only at grading time.")
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    assert admitted(review) is None
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert admitted(review)["consult_reason"].startswith("a check the Reviewer cannot make here")


SUCCESS = {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": "grouped", "success": True, "status": "done"}
FAILURE = {**SUCCESS, "success": False, "status": "error"}
VERDICT = {"type": EventType.LIFE_PLANNER_VERDICT, "status": "planned", "project_done": False}


def test_a_mission_end_or_a_planner_verdict_only_delivers_an_issued_decision(admitted, tmp_path):
    Backlog(tmp_path / "backlog.jsonl").update("grouped", pending_question="Which column holds the group?")
    for event in (SUCCESS, FAILURE, VERDICT, _review(1)):
        assert admitted(event) is None
    supervision._write(tmp_path / "manager-supervision" / "latest.json", {"status": "issued"})
    for event in (SUCCESS, FAILURE, VERDICT, _review(1)):
        assert "consult_reason" not in admitted(event)


def test_only_a_stalled_turn_of_the_round_stalls_reaches_the_manager(admitted):
    stall = {"type": EventType.ROUND_STALL, "round_index": 3, "semantic_stall_streak": 2,
             "stall_reason": "no_forward_progress", "consult": "2 rounds without forward progress"}
    assert admitted(stall) is None


_TURN_STALL = {
    "type": EventType.ROUND_STALL, "stall_reason": "turn_stalled", "item_id": "grouped",
    "idle_seconds": 1800, "running_tool": "sleep 9999",
    "text": "the Engineer's turn has made no progress for 30 min while running `sleep 9999`",
}


def test_a_stalled_turn_reaches_the_manager_with_its_choices(admitted):
    event = admitted(_TURN_STALL)
    assert event["consult_reason"].startswith("the Engineer's turn has made no progress for 30 min")
    assert "CONTINUE keeps waiting on that turn, STEER stops it" in event["consult_reason"]


_REMARK = {
    "type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "to": "manager",
    "text": "the planner woke 14 times on the same running job (events.jsonl ts 1760000000)",
}


def test_an_observer_remark_to_the_manager_reaches_its_supervision(admitted):
    event = admitted(_REMARK)
    assert event["consult_reason"] == f"the Observer, who watches how the team works, says: {_REMARK['text']}"
    assert event["stall_reason"] == "observer"
    assert admitted({**_REMARK, "to": "planner"}) is None


_REPORTS = [{**_TURN_STALL, "consult_reason": "stalled"}, {**_REMARK, "stall_reason": "observer", "consult_reason": "stalled"}]


@pytest.mark.parametrize("report", _REPORTS)
def test_newer_evidence_does_not_drop_a_pending_stall_report(admitted, tmp_path, report):
    """A stall or a remark is sent once; the event that replaces it in the queue asks for both."""
    key = str(tmp_path.resolve())
    supervision._PENDING[key] = (None, tmp_path, report)
    supervision._admit(None, tmp_path, _review(4, consult_reason="review"))
    merged = supervision._PENDING.pop(key)[2]
    assert merged["type"] == EventType.ROUND_REVIEW_COMPLETED and merged["stall_reason"] == report["stall_reason"]
    assert merged["consult_reason"] == "stalled; review"


@pytest.mark.parametrize("report", _REPORTS)
def test_a_stalled_turn_is_judged_even_when_the_project_files_are_unchanged(tmp_path, report):
    """The report is the news: an applied look at the same files does not answer it."""
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend(json.dumps({"action": "continue", "reason": "The run is still plausible.", "evidence_refs": ["backlog.jsonl"]}))
    manager = Manager(tmp_path, runner=backend)
    first = supervision.supervise(manager, tmp_path, _review(2, forward_progress=False))
    assert first["status"] == "applied" and len(backend.prompts) == 1
    assert supervision.supervise(manager, tmp_path, _review(2, forward_progress=False)) == first
    stalled = supervision.supervise(manager, tmp_path, report)
    assert stalled["status"] == "applied" and stalled["id"] != first["id"]
    assert len(backend.prompts) == 2 and "consulted now because of: stalled" in backend.prompts[1]


def test_a_failed_check_does_not_count_as_the_manager_having_looked(tmp_path):
    """The next trigger on the same evidence asks again after a failed or superseded check."""
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    manager = Manager(tmp_path, runner=_Backend("I am not sure."))
    event = {**_review(2), "consult_reason": "the Engineer handed the work to you"}
    assert supervision.supervise(manager, tmp_path, event)["status"] == "failed"
    good = json.dumps({"action": "steer", "reason": "The same failure repeats; change approach.",
                       "directive": "Write a reproducible check first.", "evidence_refs": ["backlog.jsonl"]})
    manager.runner.text = good
    assert supervision.supervise(manager, tmp_path, event)["status"] == "applied"
    assert len(manager.runner.prompts) == 2


def test_the_consult_reason_reaches_the_prompt_and_the_receipt(tmp_path):
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend(json.dumps({"action": "continue", "reason": "The repair is under way.", "evidence_refs": ["backlog.jsonl"]}))
    event = {**_review(2, forward_progress=False), "consult_reason": "the Engineer handed the work to you"}
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    assert record["status"] == "applied"
    assert "consulted now because of: the Engineer handed the work to you" in backend.prompts[0]
    assert "ACTION: continue, steer, wait or stop" in backend.prompts[0]
    assert record["trigger"]["round_index"] == 2
    assert record["trigger"]["consult_reason"] == "the Engineer handed the work to you"


@pytest.mark.parametrize(("event", "planned"), [
    ({**_review(2, status="blocked", review_source="engineer_handoff"),
      "consult_reason": "the Engineer handed the work to you"}, True),
    ({**_REMARK, "stall_reason": "observer", "consult_reason": "the Observer says the Planner spins"}, False),
])
def test_only_a_steer_not_prompted_by_the_observer_reaches_the_planner(tmp_path, event, planned):
    """2026-10-20: Observer remark, Manager steer, Planner turn, remark again, four
    times over, each remark worded anew. A remark never moves the baton."""
    from argus.manager.directive import load_active_manager_directive

    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend("ACTION: steer\nREASON: Plan the grouping repair apart.\n"
                       "EVIDENCE_REFS: backlog.jsonl\nDIRECTIVE: Split the repair into two tasks.\n→ planner")
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)

    assert record["status"] == "applied" and record["decision"]["handoff"] == "planner"
    directive = load_active_manager_directive(tmp_path)
    assert bool(directive.planner_revision) is planned


def test_engineer_phase_starts_no_longer_trigger_a_check(admitted):
    assert admitted({"type": EventType.LIFE_PHASE_STARTED, "agent_layer": "engineer", "round_index": 3}) is None


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


def test_a_steer_names_the_planner_only_when_it_needs_new_planning():
    steer = "ACTION: steer\nREASON: char_count runs on its own.\nEVIDENCE_REFS: backlog.jsonl\nDIRECTIVE: Keep A/B parked."
    assert supervision._decision(steer)["handoff"] == ""
    assert supervision._decision(steer + "\n→ planner")["handoff"] == "planner"


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
    f"ACTION: STEER — do not change course yet.\nREASON: ok\n{R}\nDIRECTIVE: x",
    f"ACTION: STEER — don't, the fix landed.\nREASON: ok\n{R}\nDIRECTIVE: x",
    f"ACTION: STEER — unneeded.\nREASON: ok\n{R}\nDIRECTIVE: x",
    f"ACTION: STEER — only if round 4 repeats.\nREASON: ok\n{R}\nDIRECTIVE: x",
    f"ACTION: WAIT — hold off until the reviewer runs.\nREASON: ok\n{R}",
    f"ACTION: STEER — optional.\nREASON: ok\n{R}\nDIRECTIVE: x",
    # An exact ACTION that a DECISION contradicts.
    f"DECISION: STEER\nACTION: continue\nREASON: ok\n{R}",
])
def test_rescue_reading_never_invents_a_decision(text):
    with pytest.raises(supervision.SupervisionDecisionError):
        supervision._decision(text)


@pytest.mark.parametrize("status", ["done", "continue", "blocked"])
def test_a_workspace_changed_during_review_reaches_the_manager_whatever_the_verdict(admitted, status):
    event = admitted(_review(1, status=status, changed_during_review=["src/model.py"]))
    assert "src/model.py" in event["consult_reason"]
    assert "decide whether this review counts" in event["consult_reason"]
    assert admitted(_review(1, status=status)) is None
