"""Tests for the Manager division layer — the ticket's route / stage split / commit.

The Manager chooses the route in its ticket; Argus checks and commits it. These
tests use fake runners (no real LLM).
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from argus.manager import Manager
from argus.manager.domain_author import RouteContext, VerticalDecision
from argus.manager.form import manager_actions
from argus.skills.stage_machine import ChecklistItem
from argus.skills.vertical_select import VerticalResolutionError, persist_vertical
from argus.verticals.research.stages import STAGE_ORDER as RESEARCH_STAGES
from tests.hand_off import ticket


class _DecisionResult:
    def __init__(self, msg: str, *, tool_activity_observed: bool = True) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"
        self.tool_activity_observed = tool_activity_observed










def test_direct_software_handoff_skips_duplicate_manager_grounding(
    tmp_path,
) -> None:
    class GroundingRunner:
        def __init__(self) -> None:
            self.calls = []

        def run_exec(self, **kwargs):
            self.calls.append(kwargs)
            return _DecisionResult(
                "Architecture: parser -> loader. Closest analogue: sibling loader. "
                "Verify exact return type and boundary behavior."
            )

    runner = GroundingRunner()
    manager = Manager(project_root=tmp_path, runner=runner)

    handoff = manager._ground_execution_task(
        "Repair parser behavior.",
        workflow_mode="direct",
        root_task_id="route-1",
    )

    assert handoff == "Repair parser behavior."
    assert runner.calls == []


def test_direct_software_grounding_can_be_forced(
    tmp_path,
    monkeypatch,
) -> None:
    class GroundingRunner:
        def __init__(self) -> None:
            self.calls = []

        def run_exec(self, **kwargs):
            self.calls.append(kwargs)
            return _DecisionResult(
                "Architecture: parser -> loader. Closest analogue: sibling loader. "
                "Verify exact return type and boundary behavior."
            )

    monkeypatch.setenv("ARGUS_SKILL_SOFTWARE_REQUIRE_GROUNDING", "1")
    runner = GroundingRunner()
    manager = Manager(project_root=tmp_path, runner=runner)

    handoff = manager._ground_execution_task(
        "Repair parser behavior.",
        workflow_mode="direct",
        root_task_id="route-1",
    )

    assert "## Manager project grounding" in handoff
    assert "Closest analogue" in handoff
    assert runner.calls[0]["run_label"] == "manager-project-grounding"
    assert runner.calls[0]["options"].sandbox_mode == "read-only"
    assert runner.calls[0]["options"].dangerous_yolo is False
    assert runner.calls[0]["options"].reasoning_effort == "low"
    assert runner.calls[0]["options"].external_interrupt_reason_provider is None


def test_software_grounding_rejects_interrupted_partial_brief(
    tmp_path,
) -> None:
    class InterruptedResult(_DecisionResult):
        exit_code = 143
        fatal_error = "External interrupt: grounding budget reached"

    class GroundingRunner:
        def run_exec(self, **kwargs):
            return InterruptedResult(
                "Architecture: parser -> loader -> caller. "
                "Closest analogue: sibling_parser.py preserves the public "
                "dictionary return contract. Affected callers include the CLI "
                "and configuration loader. Verification: run the focused parser "
                "tests and probe invalid input. Acceptance risk: preserve key "
                "ordering, exception type, and valid input behavior."
            )

    manager = Manager(
        project_root=tmp_path,
        runner=GroundingRunner(),
    )
    handoff = manager._ground_execution_task(
        "Repair parser behavior.",
        workflow_mode="staged",
        root_task_id="route-1",
    )

    assert handoff == "Repair parser behavior."
    assert not hasattr(manager, "_last_software_grounding_thread_id")


















def test_the_vertical_env_cannot_stand_in_for_a_ticket(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    with pytest.raises(VerticalResolutionError, match="Waiting for the Manager's ticket"):
        Manager(project_root=tmp_path).decide_vertical("prove the lemma")


def test_plan_stages_research_is_the_four_stage_pipeline():
    stages = Manager().plan_stages("research")
    assert stages == list(RESEARCH_STAGES)
    assert stages[0] == "idea" and stages[-1] == "review"
    assert len(stages) == 4


def test_plan_stages_propagates_vertical_load_failure(monkeypatch):
    """A vertical that fails to resolve/import must PROPAGATE, not silently
    substitute the canonical/paper stage list — matches divide()'s and
    LifeSupervisor._resolve_vertical_once's documented FAIL-HARD contract.
    Silently degrading here would turn e.g. a math_synth mission into the
    paper pipeline with no visible error."""
    from argus.verticals import _base

    def _boom(name, project_root=None, *, scoped=True):
        raise RuntimeError("simulated broken vertical import")

    monkeypatch.setattr(_base, "load_vertical", _boom)
    with pytest.raises(RuntimeError, match="simulated broken vertical import"):
        Manager().plan_stages("math_synth")


def test_plan_stages_rejects_incomplete_vertical_contract(monkeypatch):
    """Missing stages fail visibly instead of becoming another vertical."""
    from argus.core.vertical_contract import VerticalContractError
    from argus.verticals import _base

    class _BareModule:
        pass

    monkeypatch.setattr(_base, "load_vertical", lambda name, project_root=None, scoped=True: _BareModule())
    with pytest.raises(VerticalContractError, match="declares no stage order"):
        Manager().plan_stages("some-vertical")








def test_target_capable_vertical_parsing_is_not_math_specific() -> None:
    decision = RouteContext(known_verticals=("physics",), research_target_verticals=("physics",)).decide({
        "to": "team", "summary": "a physics derivation", "assumptions": [], "choice": "existing",
        "vertical": "physics", "execution_task": "derive the requested result",
        "research_target_level": "doctoral",
    })

    assert decision.vertical == "physics"
    assert decision.research_target_level == "doctoral"


def test_vertical_commit_persists_generic_research_target_contract(
    tmp_path,
    monkeypatch,
    optimize_vertical,
) -> None:
    from types import SimpleNamespace

    from argus.verticals import _base

    monkeypatch.setattr(
        _base,
        "load_vertical",
        lambda name, project_root=None, scoped=True: SimpleNamespace(
            STAGE_ORDER=("scope", "review"),
            CHECKLIST_STAGE_ORDER=("scope", "review"),
            CHECKLIST_ITEMS={
                "scope": (
                    ChecklistItem("scope.goal", "Goal is explicit", "goal"),
                ),
                "review": (
                    ChecklistItem("review.result", "Result is reviewed", "result"),
                ),
            },
            completion_gate="none",
            RESEARCH_TARGET_LEVELS=("exploratory", "publishable", "doctoral"),
        ),
    )
    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing",
        vertical=optimize_vertical,
        execution_task="derive the requested result",
        research_target_level="doctoral",
    )

    division = manager.commit_vertical_decision(
        "derive the requested result",
        decision,
    )

    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert division.vertical == optimize_vertical
    assert state["research_target_level"] == "doctoral"
    assert state["research_target_set_at"] > 0




def test_replacement_intent_forces_immediate_pipeline_reset(tmp_path):
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "vertical": "learning",
            "current_stage": "review",
            "stages": {
                "ingest": {"status": "done"},
                "study": {"status": "done"},
                "curate": {"status": "done"},
                "review": {"status": "in_progress"},
            },
        }),
        encoding="utf-8",
    )
    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing",
        vertical="research",
        execution_task="select a real open Erdos problem",
    )

    manager.commit_vertical_decision(
        "replace the old closed theorem target",
        decision,
        force_stage_reset=True,
    )

    state = json.loads(state_path.read_text())
    assert state["vertical"] == "research"
    assert state["current_stage"] == "idea"
    assert state["research_intent_generation"] == 2
    assert state["stages"]["review"]["status"] == "pending"
    assert state["stage_history"][-1]["direction"] == "reset"


@pytest.mark.parametrize("completed", [False, True])
def test_new_intent_rejects_prior_same_target_certification(
    tmp_path,
    monkeypatch,
    completed: bool,
) -> None:
    from argus.life.memory import EventJournal
    from argus.life.supervisor._planning_cycle_helpers import (
        _research_project_done_issue,
    )

    state_root = tmp_path / "state"
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setattr("argus.skills.vertical_select.time.time", lambda: 100.0)
    persist_vertical(state_root, "research", research_target_level="exploratory")
    state_path = state_root / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "review"
    state["stages"] = {
        "idea": {"status": "done"},
        "experiment": {"status": "done"},
        "paper": {"status": "done"},
        "review": {"status": "done" if completed else "in_progress"},
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    certification = {
        "id": "old-certification",
        "type": "life.mission.completed",
        "ts": 200.0,
        "scope": "final_submission",
        "final_submission_certified": True,
    }
    journal = EventJournal(state_root / "events.jsonl")
    journal.path.write_text(json.dumps(certification) + "\n", encoding="utf-8")
    assert _research_project_done_issue(
        state_root, journal.all(), evidence_root=workdir
    ) == ""

    monkeypatch.setattr("argus.skills.vertical_select.time.time", lambda: 300.0)
    Manager(project_root=state_root, execution_workdir=workdir).commit_vertical_decision(
        "prove a different theorem",
        VerticalDecision(
            choice="existing",
            vertical="research",
            research_target_level="exploratory",
        ),
        force_stage_reset=not completed,
    )

    assert _research_project_done_issue(
        state_root, journal.all(), evidence_root=workdir
    ) == "missing_exploratory_reviewer_certification"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["research_target_set_at"] == 300.0
    assert state["current_stage"] == "idea"

    certification.update(id="new-certification", ts=400.0)
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(certification) + "\n")
    assert _research_project_done_issue(
        state_root, journal.all(), evidence_root=workdir
    ) == ""


def test_replacement_intent_can_commit_a_supplied_locked_idea(tmp_path) -> None:
    persist_vertical(
        tmp_path,
        "research",
        research_target_level="publishable",
        research_direction_mode="broad",
        workflow_mode="staged",
    )
    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing",
        vertical="research",
        execution_task="write a paper from the supplied method",
        workflow_mode="staged",
        research_target_level="publishable",
        research_direction_mode="locked",
    )

    manager.commit_vertical_decision(
        "replace discovery with the operator's supplied paper idea",
        decision,
        force_stage_reset=True,
    )

    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert state["research_direction_mode"] == "locked"
    assert state["current_stage"] == "idea"


@pytest.mark.parametrize("start_stage", ["paper", "review"])
def test_completed_paper_artifact_revision_keeps_direction_and_selected_start(
    tmp_path, monkeypatch, start_stage,
) -> None:
    from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state

    persist_vertical(
        tmp_path, "research", workflow_mode="staged",
        research_target_level="publishable", research_direction_mode="broad",
    )
    state = read_pipeline_state(tmp_path)
    state.update(
        current_stage="review", current_verdict="certified",
        stages={stage: {"status": "done"} for stage in RESEARCH_STAGES},
        selected_idea={"route": "previously-reviewed-method"},
        research_target_set_at=1.0,
    )
    write_pipeline_state(tmp_path, state)
    notes = tmp_path / "RESEARCH_NOTES.md"
    notes.write_text("Existing scientific result and evidence pointers.")
    # The completed-paper certificate is an input to this dispatch test;
    # certificate validation has independent coverage.
    monkeypatch.setattr(
        "argus.manager._vertical_ops.vertical_select.vertical_reached_own_terminal_stage",
        lambda *_args: True,
    )
    manager = Manager(project_root=tmp_path)
    decision = VerticalDecision(
        choice="existing", vertical="research", workflow_mode="direct",
        start_stage=start_stage, execution_task="Redraw the existing framework figure",
        research_target_level="publishable", research_direction_mode="locked",
    )
    manager.commit_vertical_decision("Redraw the paper's framework figure", decision)

    state = read_pipeline_state(tmp_path)
    assert state["current_stage"] == start_stage
    assert state["current_verdict"] == "in_progress"
    assert state["research_direction_mode"] == "broad"
    assert state["selected_idea"] == {"route": "previously-reviewed-method"}
    assert state["research_target_set_at"] > 1.0
    assert notes.read_text() == "Existing scientific result and evidence pointers."
    assert state["stages"]["review"]["status"] != "done"








def test_root_task_id_scopes_manager_stage_call(tmp_path):
    from argus.core.models import ReviewDecision

    transitions: list[tuple[str, str]] = []

    @contextmanager
    def usage_context(root_task_id: str):
        transitions.append(("enter", root_task_id))
        try:
            yield
        finally:
            transitions.append(("exit", root_task_id))

    (tmp_path / ".argus").mkdir()
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "current_stage": "research"}),
        encoding="utf-8",
    )
    review = ReviewDecision(
        status="continue",
        reason="more evidence needed",
        next_action="continue",
    )
    manager = Manager(
        project_root=tmp_path,
        usage_context=usage_context,
    )

    manager.decide_stage_transition(
        review=review,
        project_root=tmp_path,
        run_exec=lambda prompt: _DecisionResult(json.dumps({
            "action": "hold",
            "target_stage": "research",
            "reason": "continue",
        })),
        root_task_id="root-task-3",
    )

    assert transitions == [
        ("enter", "root-task-3"),
        ("exit", "root-task-3"),
    ]


















@pytest.mark.parametrize("ask_on_new_domain", [False, True])
def test_new_domain_start_stage_survives_commit_and_confirmation(
    tmp_path, ask_on_new_domain,
) -> None:
    task = "Validate the supplied field report."
    decision = RouteContext().decide({
        "to": "team", "summary": "a new field-report capability", "assumptions": [],
        "choice": "new", "vertical": "field_report", "workflow_mode": "direct",
        "start_stage": " VaLiDaTe ", "execution_task": task,
    })
    manager = Manager(project_root=tmp_path)

    division = manager.commit_vertical_decision(
        task, decision, ask_on_new_domain=ask_on_new_domain,
    )
    if ask_on_new_domain:
        division = manager.commit_domain(
            division.task,
            division.proposed_domain,
            execution_task=division.execution_task,
            workflow_mode=division.workflow_mode,
            start_stage=division.start_stage,
        )

    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert division.workflow_mode == "direct"
    assert state["current_stage"] == "validate"
























def test_a_ticket_s_work_order_reaches_the_route_whole() -> None:
    routed: list = []
    actions = manager_actions("write it up", active_mission=False, route=routed.append)

    actions.dispatch(*ticket("x" * 9000))

    assert routed[0]["execution_task"].startswith("x" * 9000 + "\n\nAcceptance:")


def _ticket(vertical: str, task: str = "perform the requested task", **fields) -> dict:
    """The route a Manager ``manager`` ticket carries."""
    payload = {
        "vertical": vertical, "workflow_mode": "direct" if vertical == "software" else "staged",
        "execution_task": task, "summary": "the Manager's ticket", "assumptions": [],
    }
    if vertical == "research":
        payload["research_target_level"] = "publishable"
    return {**payload, **fields}


def _divide(manager: Manager, task: str, vertical: str):
    """What a ticket does: its route checked, then committed."""
    return manager.commit_vertical_decision(task, manager.adopt_route(_ticket(vertical, task)))


def test_a_ticket_conflicting_with_the_persisted_staged_mode_is_refused(tmp_path) -> None:
    persist_vertical(tmp_path, "software", workflow_mode="staged")

    with pytest.raises(ValueError, match="workflow_mode must stay staged"):
        Manager(project_root=tmp_path).adopt_route(_ticket("software", "repair the repository"))


def test_a_new_handoff_may_raise_the_persisted_route_contract(tmp_path) -> None:
    persist_vertical(tmp_path, "research", workflow_mode="direct", research_target_level="exploratory",
                     research_direction_mode="locked")
    stronger = _ticket("research", "run real experiments for the stronger objective",
                       research_direction_mode="broad")

    decision = Manager(project_root=tmp_path).adopt_route(stronger, allow_route_contract_change=True)

    assert (decision.vertical, decision.workflow_mode) == ("research", "staged")
    assert (decision.research_target_level, decision.research_direction_mode) == ("publishable", "broad")


def test_work_already_routed_keeps_a_legacy_direct_alias(tmp_path) -> None:
    state = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"vertical": "direct"}', encoding="utf-8")

    decision = Manager(project_root=tmp_path).decide_vertical("repair the repository")

    assert (decision.vertical, decision.workflow_mode) == ("software", "direct")


def test_work_already_routed_keeps_its_domain_and_research_target(tmp_path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry", workflow_mode="staged",
                     research_target_level="publishable")

    decision = Manager(project_root=tmp_path).decide_vertical("continue the paper")

    assert (decision.domain, decision.research_target_level) == ("chemistry", "publishable")


def test_a_research_ticket_commits_its_target_and_stages(tmp_path) -> None:
    division = _divide(Manager(project_root=tmp_path), "draft a paper for EMNLP submission", "research")

    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert division.vertical == state["vertical"] == "research"
    assert division.stages == list(RESEARCH_STAGES)
    assert "vertical=research" in division.headline()
    assert state["research_target_level"] == "publishable"
    assert state["research_target_set_at"] > 0


def test_failed_vertical_commit_restores_pipeline_state(tmp_path, monkeypatch, optimize_vertical) -> None:
    manager = Manager(project_root=tmp_path)
    _divide(manager, "seed the research pipeline", "research")
    pipeline_state = tmp_path / ".argus" / "PIPELINE_STATE.json"
    before = pipeline_state.read_bytes()
    monkeypatch.setattr(
        "argus.manager._vertical_ops.vertical_select.reset_stage_for_new_intent",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reset failed")),
    )

    with pytest.raises(RuntimeError, match="reset failed"):
        manager.commit_vertical_decision("run the synthesis pipeline", VerticalDecision(
            choice="existing", vertical=optimize_vertical, execution_task="run the synthesis pipeline",
        ))
    assert pipeline_state.read_bytes() == before


def test_a_new_intent_restarts_the_stages_a_finished_prior_vertical_left(tmp_path) -> None:
    """A finished custom vertical's terminal "review" is not progress on a new research task."""
    from argus.skills.stage_machine import current_stage
    from argus.verticals._data_domain import write_data_domain

    old_stage_order = ("investigate", "configure", "dry_run", "document", "review")
    write_data_domain(tmp_path, "ops_continuity_runbook", stages=list(old_stage_order),
                      checklist_stage_order=list(old_stage_order), created_by="manager")
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(json.dumps({
        "vertical": "ops_continuity_runbook", "current_stage": "review",
        "stages": {stage: {"status": "done"} for stage in old_stage_order},
    }), encoding="utf-8")

    _divide(Manager(project_root=tmp_path), "write a brand new paper", "research")

    assert current_stage(tmp_path) == "idea"


def test_a_second_task_on_the_same_vertical_reopens_its_finished_pipeline(tmp_path) -> None:
    from argus.skills.vertical_select import vertical_reached_own_terminal_stage

    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(json.dumps({
        "vertical": "research", "current_stage": "review",
        "stages": {stage: {"status": "done"} for stage in RESEARCH_STAGES},
    }), encoding="utf-8")

    _divide(Manager(project_root=tmp_path), "start a separate second research task", "research")

    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert (state["current_stage"], state["research_intent_generation"]) == ("idea", 2)
    assert vertical_reached_own_terminal_stage(tmp_path, "research") is False
