"""When the Manager is asked to supervise, and what its handoff decides.

Only what is put to it: an Engineer handing the work to the Manager, a check whose risk the
Manager accepts in the operator's place, a stalled Engineer turn, an Observer remark,
a runtime incident. Nothing counts rounds or watches the Reviewer's
mood; a blocked verdict goes to the stage decision, a question to the
operator, and any other boundary only delivers a decision already issued.
"""
from __future__ import annotations

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.daemon.state import write_continuous_config
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager, supervision


class _Backend:
    """A Manager that hands off ``payload``, or ends its turn without a handoff."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.prompts.append(prompt)
        if self.payload:
            bridge_request(PREFIX, "hand_off", self.payload, env=options.extension_env)
        return RunnerResult(exit_code=0, call_id="offline", agent_messages=["Judged."])


def judged(summary: str, *, to: str = "team", note: str = "") -> dict:
    return {"to": to, "summary": summary, "note": note, "evidence_refs": ["backlog.jsonl"], "assumptions": []}


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


def test_an_engineer_handing_the_work_over_reaches_the_manager(admitted):
    event = admitted(_review(2, status="manager", review_source="engineer_handoff",
                             reason="The fixture needs a credential only you can grant."))
    assert event["consult_reason"] == (
        "the Engineer handed the work to you, so the process is stuck: "
        "The fixture needs a credential only you can grant."
    )
    # The Manager's rung is process debugging: the brief and its refusals apply.
    assert event["process_stuck"] is True


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
    supervision._write(tmp_path / "manager-supervision" / f"{'1' * 32}.json", {"status": "issued"})
    for event in (SUCCESS, FAILURE, VERDICT, _review(1)):
        assert "consult_reason" not in admitted(event)


_TURN_STALL = {
    "type": EventType.ROUND_STALL, "stall_reason": "turn_stalled", "item_id": "grouped",
    "idle_seconds": 1800, "running_tool": "sleep 9999",
    "text": "the Engineer's turn has made no progress for 30 min while running `sleep 9999`",
}


def test_a_stalled_turn_reaches_the_manager_with_its_choices(admitted):
    event = admitted(_TURN_STALL)
    assert event["consult_reason"].startswith("the Engineer's turn has made no progress for 30 min")
    assert "to=team without a note keeps waiting on that turn" in event["consult_reason"]


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
def test_what_is_put_to_the_manager_is_judged_even_when_the_project_files_are_unchanged(tmp_path, report):
    """The trigger is the news: an applied look at the same files does not answer it."""
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend(judged("The run is still plausible."))
    manager = Manager(tmp_path, runner=backend)
    first = supervision.supervise(manager, tmp_path, _review(2, forward_progress=False))
    again = supervision.supervise(manager, tmp_path, _review(2, forward_progress=False))
    assert first["status"] == again["status"] == "applied" and again["id"] != first["id"]
    stalled = supervision.supervise(manager, tmp_path, report)
    assert stalled["status"] == "applied" and stalled["id"] != again["id"]
    assert len(backend.prompts) == 3 and "consulted now because of: stalled" in backend.prompts[2]


def test_a_failed_check_does_not_count_as_the_manager_having_looked(tmp_path):
    """The next trigger on the same evidence asks again after a failed or superseded check."""
    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    manager = Manager(tmp_path, runner=_Backend())
    event = {**_review(2), "consult_reason": "the Engineer handed the work to you"}
    failed = supervision.supervise(manager, tmp_path, event)
    assert (failed["status"], failed["error_code"]) == ("failed", "handoff_missing")
    manager.runner.payload = judged(
        "The same failure repeats; change approach.", note="Write a reproducible check first.",
    )
    assert supervision.supervise(manager, tmp_path, event)["status"] == "applied"
    assert len(manager.runner.prompts) == 2


def test_new_planning_goes_to_the_planner(tmp_path):
    from argus.manager.directive import load_active_manager_directive

    write_continuous_config(tmp_path, enabled=True, objective="Produce a validated grouped summary")
    Backlog(tmp_path / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    backend = _Backend(judged(
        "Plan the grouping repair apart.", to="planner", note="Split the repair into two tasks.",
    ))
    event = {**_review(2, status="manager", review_source="engineer_handoff"),
             "consult_reason": "the Engineer handed the work to you"}
    record = supervision.supervise(Manager(tmp_path, runner=backend), tmp_path, event)

    assert record["status"] == "applied" and record["decision"]["action"] == "steer"
    assert load_active_manager_directive(tmp_path).planner_revision


@pytest.mark.parametrize("status", ["done", "continue", "blocked"])
def test_a_workspace_changed_during_review_puts_nothing_to_the_manager_whatever_the_verdict(admitted, status):
    """The verdict stands as written, with the changed paths recorded beside it."""
    assert admitted(_review(1, status=status, changed_during_review=["src/model.py"])) is None


# --------------------------------------------------------------------------- the supervise form

def _form(root):
    from functools import partial

    from argus.core.handoff import HandOffActions
    from argus.manager.observation import observe_project

    write_continuous_config(root, enabled=True, objective="Produce a validated grouped summary")
    Backlog(root / "backlog.jsonl").add(BacklogItem.new(item_id="grouped", title="t", objective="o"))
    return HandOffActions("supervise", check=partial(
        supervision._check, root=root, event={}, observation=observe_project(root),
    ))


@pytest.mark.parametrize(("change", "refusal"), [
    ({"evidence_refs": ["other-project/private.json"]}, "1 of 1 are not paths"),
    ({"to": "planner"}, "needs your instruction for the Planner"),
    ({"consultation": {"id": "advice-1", "disposition": "adopt"}}, "no completed advisor consultation"),
    ({"risk": {"accept": {"check": "live token", "risk": "untested"}, "revoke": "risk-0123456789"}},
     "either accept or revoke"),
    ({"risk": {"accept": {"check": "live token", "risk": "untested"}}}, "theirs to decide"),
])
def test_a_decision_the_evidence_does_not_support_is_refused_in_the_turn(tmp_path, change, refusal):
    with pytest.raises(ValueError, match=refusal):
        _form(tmp_path).dispatch("hand_off", {**judged("Decided."), **change})


def test_with_nobody_to_ask_the_manager_accepts_a_residual_risk(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    actions = _form(tmp_path)
    actions.dispatch("hand_off", {**judged("Accepted."), "risk": {"accept": {"check": "live token", "risk": "untested"}}})
    assert actions.handoff.value["risk_decision"] == {"action": "accept", "check": "live token", "risk": "untested"}
    with pytest.raises(ValueError, match="cannot be asked"):
        actions.dispatch("hand_off", {**judged("Ask.", to="operator"), "ask": {"question": "Keep A or B?", "options": []}})
