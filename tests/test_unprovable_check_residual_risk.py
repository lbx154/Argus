"""A check impossible here: grounded, accepted by its owner, and always visible.

One task's event feed took a bearer token that its packet said is supplied only
during the grader's own commands. The Reviewer agreed the token was unavailable
and still made completion depend on that run. Nothing turned "impossible here"
into a decision: the no-progress count restarted with every replan and every
replacement task, the Manager could only steer toward a check nobody could run,
and the loop went on for 15 rounds over 5 missions.

What changes, and what each test below holds:

* "impossible here" must be grounded: the Reviewer quotes the task, packet or
  environment statement that says so, and the host checks the quote;
* leaving such a check unverified changes the acceptance standard, so it is the
  operator's decision when one is available and the Manager's only when none
  is; an acceptance names one check and its item, and can be revoked;
* a fixture counts only if it follows the documented interface, runs the real
  entry point and is labelled; mocks never ship;
* the Manager's acceptance is an explicit ACCEPT_RISK: yes, never prose;
* an approval that leaves a residual risk says so everywhere completion shows.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.cli.event_format import format_event_message
from argus.core.event_catalog import EventType, validate_event_envelope
from argus.core.file_facts import file_facts
from argus.core.grounding_baseline import snapshot
from argus.core.models import RunnerResult
from argus.core.residual_risk import (
    accept_residual_risk,
    active_residual_risks,
    reviewer_block,
    revoke_residual_risk,
)
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.manager import supervision
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer.tools import ReviewGrounding, review_actions
from tests.hand_off import hand_off

_STATEMENT = "The feed token is supplied only during the grader's own commands."
_OBJECTIVE = (
    "Build the dispatch CLI; ingest reads the event feed with its bearer token. "
    + _STATEMENT
)
_OBSTACLE = (
    "Live ingestion needs the feed token, which exists only during grading. "
    "A labelled fixture of the feed run through the public ingest command is "
    "the best evidence here; live behaviour stays unverified."
)


@pytest.fixture
def no_operator(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")


def _grounding(*, operator: bool, roots: tuple[str, ...] = (), **fields) -> ReviewGrounding:
    fields.setdefault("task_text", _OBJECTIVE)
    return ReviewGrounding(roots=roots, operator_available=operator, **fields)


_MANAGER_ROW = {
    "id": "risk-0000000000", "item_id": "feed-task", "accepted_by": "manager",
    "check": "Live feed ingestion with the grading-time token", "risk": "Live feed behaviour is unverified.",
}


def _risk(**overrides) -> dict:
    return {
        "check": "Live feed ingestion with the grading-time token",
        "evidence": "Read tests/fixtures/feed_fixture.py and its recorded run through `dispatch ingest`.",
        "risk": "Live feed behaviour is unverified.",
        "impossible_because": {"quote": _STATEMENT, "source": "task"},
        "accepted_by": "manager",
        "acceptance": "risk-0000000000",
        **overrides,
    }


# --- 2. "Impossible here" is grounded in a quoted statement -----------------


def test_an_impossible_check_must_quote_the_task_packet_or_environment(tmp_path) -> None:
    (tmp_path / "DEPLOY.md").write_text("Secrets: FEED_TOKEN is injected at deploy time only.\n", encoding="utf-8")
    # Work on the objective began after DEPLOY.md was written.
    actions = review_actions(grounding=_grounding(operator=True, roots=(str(tmp_path),), baseline=snapshot((tmp_path,))))
    payload = hand_off("engineer", "No token here.", unverifiable=_OBSTACLE)[1]

    # Not in the task text: the check is missing, not impossible.
    with pytest.raises(ValueError, match="not in the task text or packet"):
        actions.dispatch("hand_off", {**payload, "impossible_because": {
            "quote": "The token will be provided by the grading harness later.", "source": "task",
        }})
    # Too short to be a statement.
    with pytest.raises(ValueError):
        actions.dispatch("hand_off", {**payload, "impossible_because": {"quote": "grader", "source": "task"}})
    # A file outside the workspace is not a source.
    with pytest.raises(ValueError, match="not a readable file"):
        actions.dispatch("hand_off", {**payload, "impossible_because": {
            "quote": _STATEMENT, "source": "../outside.md",
        }})

    actions.dispatch("hand_off", {**payload, "impossible_because": {"quote": _STATEMENT, "source": "task"}})
    assert actions.handoff.value.verification_obstacle_basis == _STATEMENT

    actions.dispatch("hand_off", {
        **payload,
        "impossible_because": {"quote": "FEED_TOKEN is injected at deploy time only", "source": "DEPLOY.md"},
    })
    assert actions.handoff.value.verification_obstacle_basis == "FEED_TOKEN is injected at deploy time only"
    payload_event = actions.handoff.value.to_event_payload(round_index=2, round_max=0, text="review")
    assert payload_event["verification_obstacle_basis"] == "FEED_TOKEN is injected at deploy time only"
    # The source travels with the quote, so the Manager sees where it came from.
    assert payload_event["verification_obstacle_basis_source"] == "DEPLOY.md"
    assert validate_event_envelope({"type": "round.review.completed", **payload_event}).errors == ()


_NOTE_QUOTE = "the feed token is supplied only at grading time"
_NOTE = {"quote": _NOTE_QUOTE, "source": "NOTES.md"}
_REVISE = hand_off("engineer", "No token here.", unverifiable=_OBSTACLE)[1]


def _mission_grounding(work: Path, **mission) -> ReviewGrounding:
    from argus.reviewer._core import _review_grounding

    config = ReviewerConfig(model="m", working_dir=str(work), artifact_root=str(work), mission_grounding=mission)
    return _review_grounding(config, task_parts=("Build the CLI that reads the event feed.",))


def test_a_file_an_earlier_mission_on_the_objective_wrote_never_grounds_impossible(tmp_path) -> None:
    from argus.core.grounding_baseline import objective_baseline

    state, work = tmp_path / "state", tmp_path / "work"
    work.mkdir()
    (work / "SPEC.md").write_text("Build the dispatch CLI.\n", encoding="utf-8")
    first = objective_baseline(state, _OBJECTIVE, (work,))
    # The first mission's Engineer leaves a note behind; the next mission (a new
    # item, a new contract, a later start) still judges against the first start.
    (work / "NOTES.md").write_text("Environment: the feed token is supplied only at grading time.\n", encoding="utf-8")
    time.sleep(0.01)
    second = objective_baseline(state, _OBJECTIVE, (work,))
    assert second == first and str((work / "NOTES.md").resolve()) not in second
    with pytest.raises(ValueError, match="NOTES.md is not as it was when work on this objective began"):
        review_actions(grounding=_mission_grounding(work, baseline=second)).dispatch(
            "hand_off", {**_REVISE, "impossible_because": _NOTE},
        )
    # A different objective begins from the workspace as it then is.
    assert str((work / "NOTES.md").resolve()) in objective_baseline(state, "Another objective", (work,))


def test_mission_grounding_carries_the_planner_record_and_the_objective_baseline(tmp_path) -> None:
    from argus.engineer.round_reviewer import mission_grounding

    work, state = tmp_path / "work", tmp_path / "state"
    (work / "docs").mkdir(parents=True)
    (work / "docs" / "GRADING.md").write_text("The feed token is issued to the grader only.\n", encoding="utf-8")
    contract = state / "missions" / "m1" / "mission.json"
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps({
        "kind": "mission_context", "created_at": time.time(),
        "context_refs": [
            {"ref": "docs/GRADING.md", "content_hash": json.dumps(file_facts(work / "docs" / "GRADING.md"))},
            {"ref": "docs/LEGACY.md", "content_hash": "a" * 64},  # an older digest records nothing
        ],
    }), encoding="utf-8")

    class _Config:
        context_packet_path = str(contract)
        state_root = str(state)

    from argus.core.grounding_baseline import objective_baseline

    baseline = objective_baseline(state, _OBJECTIVE, (work,))
    mission = mission_grounding(_Config(), item_id="", mission_ref="s", baseline=baseline)
    recorded = file_facts(work / "docs" / "GRADING.md")
    assert mission["packet_refs"] == (
        {"ref": "docs/GRADING.md", "content_hash": recorded},
        {"ref": "docs/LEGACY.md", "content_hash": None},
    )
    grounding = _mission_grounding(work, **mission)
    assert grounding.packet_refs == (("docs/GRADING.md", recorded),)
    actions = review_actions(grounding=grounding)
    actions.dispatch("hand_off", {**_REVISE, "impossible_because": {
        "quote": "The feed token is issued to the grader only", "source": "docs/GRADING.md",
    }})
    assert actions.handoff.value.verification_obstacle_basis_source == "docs/GRADING.md"


def _answered(check: str, option: str = "accept", *, note: str = "") -> dict:
    """A resolved decision card for a Reviewer question raised with accept_risk."""
    from argus.core.operator_decision import build_operator_decision
    from argus.core.residual_risk import acceptance_options

    card = build_operator_decision(
        item_id="feed-task", title="Ingest the feed", reason="impossible check",
        question="Leave the live feed check unverified?",
        options=acceptance_options(check, "Live feed behaviour is unverified."),
    )
    chosen = {"accept": card["options"][0]["id"], "keep": card["options"][1]["id"]}.get(option, option)
    return {**card, "status": "resolved", "selected_option": chosen, "note": note}


_CHECK = "Live feed ingestion with the grading-time token"


def test_with_an_operator_only_the_operator_can_accept_the_risk() -> None:
    tools = {tool["name"]: tool for tool in review_actions(grounding=_grounding(operator=True)).tools}
    description = tools["hand_off"]["inputSchema"]["properties"]["unverifiable"]["description"]
    assert "their decision" in description and "to=operator" in description
    assert "accept_risk" in description

    actions = review_actions(grounding=_grounding(operator=True, accepted_risks=(_MANAGER_ROW,)))
    with pytest.raises(ValueError, match="The operator can be asked"):
        actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk()))

    # Asking: the host adds the accept choice, bound to the named check.
    asking = review_actions(grounding=_grounding(operator=True))
    asking.dispatch(*hand_off("operator", "Only the grader has the token.", ask={"question": "Leave the live feed check unverified?", "options": []}, accept_risk={"check": _CHECK, "risk": "Live feed behaviour is unverified."}))
    options = asking.handoff.value.operator_options
    assert [option["label"] for option in options] == ["Accept this residual risk", "Keep the check required"]
    assert options[0]["id"] == "accept-risk"
    assert _CHECK in options[0]["description"]

    actions = review_actions(grounding=_grounding(operator=True, operator_decisions=(_answered(_CHECK),)))
    actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(
        accepted_by="operator", acceptance="decision-feed-task",
    )))
    decision = actions.handoff.value
    assert decision.status == "done"
    assert decision.residual_risk == (
        "Live feed ingestion with the grading-time token: Live feed behaviour is unverified. "
        "(accepted by the operator)"
    )
    assert decision.residual_risk_detail["basis"] == _STATEMENT
    assert decision.residual_risk_detail["acceptance"].startswith("decision-feed-task: the operator chose")


@pytest.mark.parametrize("card", [
    # No answer at all; the Reviewer quotes words instead.
    None,
    # The operator kept the check.
    _answered(_CHECK, "keep"),
    # A free-text answer, however agreeable, is not the accept choice.
    _answered(_CHECK, "custom", note="Yes, I accept shipping without the live feed check."),
    # Accepting a different check accepts nothing about this one.
    _answered("GPU benchmark on the grading cluster"),
    # An unanswered card.
    {**_answered(_CHECK), "status": "pending", "selected_option": ""},
])
@pytest.mark.parametrize("acceptance", [
    # Argus's own OperatorContext boilerplate.
    "The current task and explicit user instructions remain in force.",
    # An unrelated operator sentence.
    "Please make sure the live feed ingestion works with the real token.",
    "Yes, I accept shipping without the live feed check; the grader has the token.",
])
def test_operator_words_or_boilerplate_never_read_as_acceptance(tmp_path, monkeypatch, card, acceptance) -> None:
    from argus.core.operator_context import build_operator_context_block

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    block, _revision = build_operator_context_block("reviewer", tmp_path)
    assert "The current task and explicit user instructions remain in force." in block
    grounding = _grounding(
        operator=True, task_text=f"{_OBJECTIVE}\n{block}\n- message: {acceptance}",
        operator_decisions=() if card is None else (card,),
    )
    with pytest.raises(ValueError, match="The operator has not accepted this check"):
        review_actions(grounding=grounding).dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(accepted_by="operator", acceptance=acceptance)))


def test_without_an_operator_only_a_recorded_manager_acceptance_counts() -> None:
    tools = {tool["name"]: tool for tool in review_actions(grounding=_grounding(operator=False)).tools}
    description = tools["hand_off"]["inputSchema"]["properties"]["unverifiable"]["description"]
    assert "the Manager sees this and decides whether to accept that risk" in description

    with pytest.raises(ValueError, match="listed under Accepted residual risk"):
        review_actions(grounding=_grounding(operator=False)).dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk()),
        )
    # A risk id merely mentioned in text the Reviewer saw is not a recorded acceptance.
    with pytest.raises(ValueError, match="listed under Accepted residual risk"):
        review_actions(grounding=_grounding(
            operator=False, task_text=f"{_OBJECTIVE}\n- [risk-0000000000] accepted",
        )).dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk()))
    with pytest.raises(ValueError, match="The operator cannot be asked"):
        review_actions(grounding=_grounding(operator=False)).dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(
                accepted_by="operator", acceptance="I accept it, ship it now please",
            )),
        )
    actions = review_actions(grounding=_grounding(operator=False, accepted_risks=(_MANAGER_ROW,)))
    actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk()))
    assert actions.handoff.value.residual_risk.endswith("(accepted by the Manager)")
    # The id accepts its own check only.
    with pytest.raises(ValueError, match="accepts the check"):
        actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(check="GPU benchmark")))
    # An ungrounded basis is refused even with an acceptance.
    with pytest.raises(ValueError, match="missing, not impossible"):
        actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(
            impossible_because={"quote": "the token cannot be obtained in any environment", "source": "task"},
        )))


# --- 3 and 7. Fixture faithfulness, everywhere a fixture may stand in ------


# --- 2 and 6. Applying the Manager's decision: grounded, scoped, revocable --


class _ManagerBackend:
    """A Manager that hands off ``payload``; a refused call is the model giving up."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.refusals: list[str] = []

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        try:
            bridge_request(PREFIX, "hand_off", self.payload, env=options.extension_env)
        except ValueError as refusal:
            self.refusals.append(str(refusal))
        return RunnerResult(exit_code=0, call_id="risk", thread_id=resume_thread_id or "risk-session",
                            agent_messages=["Judged."])


def _supervised_project(root: Path, *, basis: str = _STATEMENT):
    from argus.daemon.state import write_continuous_config
    from argus.life.memory import Backlog, BacklogItem

    write_continuous_config(root, enabled=True, objective=_OBJECTIVE)
    item = BacklogItem.new(item_id="feed-task", title="Ingest the feed", objective=_OBJECTIVE)
    Backlog(root / "backlog.jsonl").add(item)
    event = {
        "type": EventType.ROUND_REVIEW_COMPLETED, "item_id": item.id, "status": "continue",
        "review_source": "reviewer", "reason": "No token here.", "verification_obstacle": _OBSTACLE,
        **({"verification_obstacle_basis": basis, "verification_obstacle_basis_source": "task"} if basis else {}),
    }
    return item, event


def _supervise(root: Path, event: dict, backend: _ManagerBackend) -> dict:
    from argus.manager import Manager
    from argus.manager.supervision import supervise

    return supervise(Manager(root, runner=backend), root, event)


_ACCEPT = {
    "to": "team", "summary": "Live ingestion needs a grading-time token; the fixture is the best evidence here.",
    "evidence_refs": ["backlog.jsonl"], "assumptions": [],
    "risk": {"accept": {
        "check": "Live feed ingestion with the grading-time token", "risk": "Live feed behaviour is unverified.",
    }},
}


def test_the_manager_accepts_a_grounded_check_for_its_item_only_without_an_operator(tmp_path, no_operator) -> None:
    item, event = _supervised_project(tmp_path)
    record = _supervise(tmp_path, event, _ManagerBackend(_ACCEPT))
    assert record["status"] == "applied", record
    accepted = record["effects"]["residual_risk_accepted"]
    (row,) = active_residual_risks(tmp_path, item_id=item.id)
    assert accepted.startswith(f"[{row['id']}]")
    assert row["item_id"] == item.id and row["accepted_by"] == "manager" and row["basis"] == _STATEMENT
    assert row["basis_source"] == "task" and record["effects"]["residual_risk_basis_source"] == "task"
    # Scoped to its item: another item's Reviewer is not told.
    assert active_residual_risks(tmp_path, item_id="other-task") == []
    assert reviewer_block(tmp_path, item_id="other-task") == ""
    block = reviewer_block(tmp_path, item_id=item.id)
    assert f"[{row['id']}]" in block and "Every other check keeps its standard" in block
    # Revocable by the Manager's next decision.
    effects: dict = {}
    later = {"id": "b" * 64, "trigger": {"item_id": item.id}, "decision": {
        "reason": "The token arrived.", "risk_decision": {"action": "revoke", "risk_id": row["id"]},
    }}
    supervision._apply_risk_decision(tmp_path, later, event, None, effects)
    assert effects == {"residual_risk_revoked": row["id"]}
    assert active_residual_risks(tmp_path, item_id=item.id) == []
    assert reviewer_block(tmp_path, item_id=item.id) == ""


def test_the_manager_cannot_accept_with_an_operator_or_without_a_quoted_basis(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    item, event = _supervised_project(tmp_path / "with-operator")
    backend = _ManagerBackend(_ACCEPT)
    record = _supervise(tmp_path / "with-operator", event, backend)
    assert record["error_code"] == "handoff_missing" and "theirs to decide" in backend.refusals[0]
    assert active_residual_risks(tmp_path / "with-operator", item_id=item.id) == []

    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    item, event = _supervised_project(tmp_path / "ungrounded", basis="")
    record = _supervise(tmp_path / "ungrounded", event, _ManagerBackend(_ACCEPT))
    assert record["effects"]["residual_risk_refused"].startswith("no Reviewer of this item quoted a statement")
    assert active_residual_risks(tmp_path / "ungrounded", item_id=item.id) == []


def test_saying_dont_ask_gives_the_manager_risk_authority(tmp_path, monkeypatch) -> None:
    """Someone must decide a check impossible here: the operator while a
    question can reach them, the Manager once they said not to be asked.
    The Manager and the Reviewer read the same switch.
    """
    from argus.core.autonomy import operator_questions_can_be_asked
    from argus.core.knob_store import write_persisted_knob
    from argus.engineer.round_reviewer import mission_grounding

    root = tmp_path / "project"
    item, event = _supervised_project(root)
    assert operator_questions_can_be_asked(root) is True
    assert supervision._manager_holds_operator_authority(root) is False

    # The operator says not to be asked: the Manager may take the decision,
    # and taking it is recorded.
    write_persisted_knob("ARGUS_SKILL_OPERATOR_AVAILABLE", "0")
    assert operator_questions_can_be_asked(root) is False
    record = _supervise(root, event, _ManagerBackend(_ACCEPT))
    assert record["status"] == "applied", record
    assert "residual_risk_refused" not in record["effects"]
    (row,) = active_residual_risks(root, item_id=item.id)

    # The Reviewer's grounding follows the same rule: it may cite the
    # Manager's acceptance, and is not sent to an operator nobody may ask.
    class _Config:
        context_packet_path = ""
        state_root = str(root)

    from argus.reviewer._core import _review_grounding

    mission = mission_grounding(_Config(), item_id=item.id, mission_ref="s", baseline=None)
    assert mission["operator_questions_allowed"] is False
    grounding = _review_grounding(
        ReviewerConfig(model="m", working_dir=str(tmp_path), mission_grounding=mission),
        task_parts=(_OBJECTIVE,),
    )
    assert grounding.operator_available is False
    actions = review_actions(grounding=grounding)
    actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(
        check=row["check"], acceptance=row["id"],
    )))
    assert actions.handoff.value.status == "done"
    with pytest.raises(ValueError, match="only the Manager can accept"):
        actions.dispatch(*hand_off("done", "Fixture read.", residual_risk=_risk(
            accepted_by="operator", acceptance="decision-feed-task",
        )))


def test_acceptances_are_per_check_idempotent_and_survive_revocation_as_a_record(tmp_path) -> None:
    first = accept_residual_risk(tmp_path, check="Live feed", risk="unverified", accepted_by="manager",
                                 source_ref="manager.supervision:a", item_id="item-1")
    again = accept_residual_risk(tmp_path, check="Live feed", risk="unverified", accepted_by="manager",
                                 source_ref="manager.supervision:a", item_id="item-1")
    assert first == again
    # With no item, an acceptance holds in its own mission only, never for every item.
    itemless = accept_residual_risk(tmp_path, check="GPU benchmark", risk="unmeasured", accepted_by="operator",
                                    source_ref="operator:x", mission_ref="mission-a")
    assert [row["id"] for row in active_residual_risks(tmp_path, item_id="item-1")] == [first["id"]]
    assert active_residual_risks(tmp_path, item_id="item-2") == []
    assert [row["id"] for row in active_residual_risks(tmp_path, mission_ref="mission-a")] == [itemless["id"]]
    assert active_residual_risks(tmp_path, mission_ref="mission-b") == []
    assert active_residual_risks(tmp_path) == []
    # An acceptance with neither an item nor a mission is not recorded at all.
    assert accept_residual_risk(tmp_path, check="x", risk="y", accepted_by="operator", source_ref="s") is None
    assert accept_residual_risk(tmp_path, check="", risk="x", accepted_by="manager", source_ref="s") is None
    assert accept_residual_risk(tmp_path, check="x", risk="y", accepted_by="engineer", source_ref="s") is None
    revoked = revoke_residual_risk(tmp_path, first["id"], revoked_by="operator", reason="token now available")
    assert revoked["revoked_at"] and revoked["revoke_reason"] == "token now available"
    assert revoke_residual_risk(tmp_path, "risk-ffffffffff", revoked_by="operator") is None
    # The same decision replayed does not resurrect it.
    accept_residual_risk(tmp_path, check="Live feed", risk="unverified", accepted_by="manager",
                         source_ref="manager.supervision:a", item_id="item-1")
    assert first["id"] not in {row["id"] for row in active_residual_risks(tmp_path, item_id="item-1")}


# --- 5. A mission over the objective -----------------------------------------


def _project(tmp_path: Path, objective: str = _OBJECTIVE) -> Path:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    (root / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": objective, "open_ended": False, "generation": 1}),
        encoding="utf-8",
    )
    return root


def _engineer(backend: MemoryBackend) -> SupervisedEngineer:
    return SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )


def _mission(backend: MemoryBackend, root: Path, tmp_path: Path, verdicts: list[tuple[str, dict]], session_id: str = ""):
    for index, (action, payload) in enumerate(verdicts, start=1):
        backend.queue(f"engineer-r{index}", CannedResponse(message=f"round {index}: nothing new to run"))
        backend.queue("reviewer", CannedResponse(action=(action, payload)))
    events: list[dict] = []
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    status, rounds, _final, _reason, _thread = _engineer(backend).run(
        objective=_OBJECTIVE,
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(
            max_rounds=0, state_root=root, session_id=session_id,
        ),
        workdir=workdir,
        on_event=events.append,
    )
    return status, rounds, events


# --- 1. A residual risk is visible wherever completion shows ---------------


def test_an_accepted_risk_reaches_the_reviewer_and_its_approval_is_never_plain_verified(tmp_path, no_operator) -> None:
    root = _project(tmp_path)
    entry = accept_residual_risk(root, check=_CHECK, risk="Live feed behaviour is unverified.",
                                 accepted_by="manager", source_ref="manager.supervision:z", basis=_STATEMENT,
                                 mission_ref="mission-a")
    backend = MemoryBackend()
    status, rounds, events = _mission(backend, root, tmp_path, [hand_off("done", "Read tests/fixtures/feed_fixture.py and its recorded run.", residual_risk=_risk(acceptance=entry["id"]))], session_id="mission-a")
    assert status == "done", events[-3:]
    prompt = [p for label, p, _ in backend.history if label == "reviewer"][0]
    assert f"[{entry['id']}]" in prompt and "## Accepted residual risk" in prompt
    done = [e for e in events if e.get("type") == "round.review.completed"][-1]
    assert done["residual_risk"].endswith("(accepted by the Manager)")
    assert validate_event_envelope(done).errors == ()
    line = format_event_message(done)
    assert "verified with residual risk: Live feed ingestion" in line
    assert "✅ verified" not in line


def test_the_mission_summary_event_and_delivery_card_name_the_residual_risk(tmp_path) -> None:
    from dataclasses import dataclass

    from argus.life.memory import BacklogItem, LifeMemory
    from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    @dataclass
    class _Outcome:
        success: bool = True
        status: str = "done"
        stop_reason: str = ""
        stop_kind: str | None = None
        recoverable: bool = False
        rounds: int = 1
        final_review_status: str = "done"
        final_review_source: str = "reviewer"
        final_review_reason: str = "Read the fixture test and its run."
        final_residual_risk: str = "Live feed ingestion: Live feed behaviour is unverified. (accepted by the operator)"
        final_message: str = ""
        summary: str = "Built ingest; the fixture run passes."
        final_output: str = ""

    class _Runner:
        def execute(self, **kwargs):
            return _Outcome()

    class _Sink:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def handle_event(self, event: dict) -> None:
            self.events.append(event)

    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=LifeMemory.open(tmp_path / "life"), runner=_Runner(), sink=sink,
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=1), poll_interval_seconds=0.01),
    )
    workdir = supervisor._project_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "dispatch.py").write_text("print('ingest')\n", encoding="utf-8")
    supervisor.memory.backlog.add(BacklogItem.new(
        title="Ingest the feed", objective="Build dispatch.py", tags=["manager_direct", "scope:bounded", "review:required"],
    ))
    supervisor.runner.execute = lambda **kwargs: _Outcome(final_output="Created `dispatch.py`.")
    supervisor.tick()

    completed = next(event for event in sink.events if event.get("type") == "life.mission.completed")
    risk = _Outcome.final_residual_risk
    assert completed["residual_risk"] == risk
    assert completed["summary"].startswith(f"Verified with residual risk: {risk}.")
    assert completed["delivery"]["residual_risk"] == risk
    assert completed["delivery"]["summary"].startswith("Verified with residual risk:")
    line = format_event_message(completed)
    assert "Completed with residual risk: Ingest the feed." in line
    assert risk in line
