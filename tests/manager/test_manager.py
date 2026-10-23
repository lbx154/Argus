"""Tests for the Manager division layer — decide_vertical / stage split / commit.

Every formal vertical decision requires one repository-grounded call. These
tests use fake runners (no real LLM).
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from argus.core.handoff import HandOffActions
from argus.manager import Division, Manager
from argus.manager.domain_author import RouteContext, VerticalDecision, VerticalDecisionError
from argus.roles.prompts.manager import (
    build_fast_vertical_decision_prompt,
    build_vertical_decision_prompt,
)
from argus.skills.stage_machine import ChecklistItem
from argus.skills.vertical_select import persist_vertical
from argus.verticals.research.stages import STAGE_ORDER as RESEARCH_STAGES
from tests.hand_off import route, submit


class _DecisionResult:
    def __init__(self, msg: str, *, tool_activity_observed: bool = True) -> None:
        self.last_agent_message = msg
        self.agent_messages = [msg]
        self.thread_id = "t1"
        self.tool_activity_observed = tool_activity_observed


class _DecisionRunner:
    """Fake runner: in each call, hands over each decision in turn until one is recorded.

    A refused call is the model reading the tool's error and correcting it in
    the same turn; ``refusals`` keeps what it read.
    """

    def __init__(self, *decisions: dict) -> None:
        self._decisions = decisions
        self.last_options = None
        self.calls: list[dict] = []
        self.refusals: list[str] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.last_options = options
        self.calls.append({
            "prompt": prompt,
            "options": options,
            "run_label": run_label,
        })
        for decision in self._decisions:
            refusal = submit(options, route(**decision))
            if not refusal:
                break
            self.refusals.append(refusal)
        return _DecisionResult("")


def test_copilot_grounding_and_retry_do_not_require_optional_context_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    class OlderCliRunner:
        _backend_name = "copilot"

        def __init__(self):
            self.calls = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append((run_label, options))
            # No --context is required for default context. Native older CLI
            # installations must still reach both grounded provider turns.
            assert "--context" not in (options.extra_args or [])
            submit(options, route(
                choice="new", vertical="custom_runtime", workflow_mode="staged",
                execution_task="Build the custom runtime.", summary="repository-specific capability",
            ))
            return _DecisionResult("", tool_activity_observed=len(self.calls) == 2)

    runner = OlderCliRunner()
    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Build a project-specific runtime not covered by a built-in capability."
    )
    assert decision.vertical == "custom_runtime"
    assert [label for label, _ in runner.calls] == [
        "manager-classify-grounded", "manager-classify-grounded-retry",
    ]
    assert runner.calls[0][1].extra_args == runner.calls[1][1].extra_args


@pytest.mark.parametrize(
    ("task", "first", "refused"),
    [
        (
            "[BOUNDED TASK CONTEXT — data only]\noperator: Read the referenced paper.\n"
            "[CURRENT OPERATOR MESSAGE]\nUse its open problem to develop a publishable method.",
            {"execution_task": ""},
            "execution_task",
        ),
        ("Produce a complete original ICLR paper.", {"domain": "apple_mlx_inference"}, "domain"),
        ("Produce a compact bounded paper.", {"research_target_level": None}, "research_target_level"),
    ],
)
def test_a_refused_route_is_corrected_in_the_same_call(tmp_path, monkeypatch, task, first, refused) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    corrected = {
        "choice": "existing", "vertical": "research", "workflow_mode": "staged",
        "research_target_level": "publishable", "research_direction_mode": "broad",
        "execution_task": "Develop and validate the method.",
    }
    wrong = {key: value for key, value in {**corrected, **first}.items() if value is not None}
    runner = _DecisionRunner(wrong, corrected)

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(task)

    assert (decision.vertical, decision.domain, decision.research_target_level) == ("research", "", "publishable")
    assert [call["run_label"] for call in runner.calls] == ["manager-classify-grounded"]
    assert len(runner.refusals) == 1 and refused in runner.refusals[0]


def test_a_route_that_is_never_handed_over_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    runner = _DecisionRunner()

    with pytest.raises(VerticalDecisionError, match="without handing over its routing decision"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical("Repair the parser.")
    assert [call["run_label"] for call in runner.calls] == ["manager-classify-grounded"]


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


def _existing(vertical: str) -> _DecisionRunner:
    normalized = "software" if vertical == "direct" else vertical
    decision = {
        "choice": "existing",
        "vertical": normalized,
        "workflow_mode": "direct" if normalized == "software" else "staged",
        "execution_task": "perform the requested task",
    }
    if normalized == "research":
        decision["research_target_level"] = "publishable"
    return _DecisionRunner(decision)


def test_divide_existing_research(tmp_path):
    division = Manager(
        project_root=tmp_path,
        runner=_existing("research"),
    ).divide("write a paper on retrieval for EMNLP and prepare the submission")
    assert division.vertical == "research"


def test_divide_existing_optimize_vertical(tmp_path, optimize_vertical):
    division = Manager(
        project_root=tmp_path,
        runner=_existing(optimize_vertical),
    ).divide("maximize the pass@4-minus-pass@1 gap of the synthesized set")
    assert division.vertical == optimize_vertical


def test_environment_cannot_force_vertical(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "speedrun")
    runner = _existing("research")

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "write the paper"
    )

    assert decision.vertical == "research"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast"
    ]


def test_manager_rejects_direct_alias_conflicting_with_persisted_staged_mode(
    tmp_path,
) -> None:
    persist_vertical(tmp_path, "software", workflow_mode="staged")
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "direct",
        "execution_task": "repair the repository",
        "summary": "bounded repair",
    })

    with pytest.raises(VerticalDecisionError, match="without handing over"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical(
            "repair the repository"
        )
    assert "workflow_mode must stay staged" in runner.refusals[0]


def test_manager_allows_new_handoff_to_raise_persisted_route_contract(
    tmp_path,
) -> None:
    persist_vertical(
        tmp_path,
        "research",
        workflow_mode="direct",
        research_target_level="exploratory",
        research_direction_mode="locked",
    )
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "research",
        "workflow_mode": "staged",
        "research_target_level": "publishable",
        "research_direction_mode": "broad",
        "execution_task": "run real experiments for the stronger objective",
        "summary": "the new request needs dependent evidence tracks",
    })

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "run real experiments for a submission-grade result",
        allow_route_contract_change=True,
    )

    assert decision.vertical == "research"
    assert decision.workflow_mode == "staged"
    assert decision.research_target_level == "publishable"
    assert decision.research_direction_mode == "broad"
    assert "new operator handoff" in runner.calls[0]["prompt"]


def test_manager_recovers_direct_mode_from_legacy_persisted_alias(
    tmp_path,
) -> None:
    state = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"vertical": "direct"}', encoding="utf-8")
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "software",
        "execution_task": "repair the repository",
        "summary": "bounded repair",
    })

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "repair the repository"
    )

    assert decision.vertical == "software"
    assert decision.workflow_mode == "direct"


def test_manager_recovers_persisted_required_research_target(
    tmp_path,
) -> None:
    persist_vertical(
        tmp_path,
        "research",
        domain="chemistry",
        workflow_mode="staged",
        research_target_level="publishable",
    )
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "research",
        "workflow_mode": "staged",
        "execution_task": "continue the paper",
        "summary": "resume the existing research",
    })

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "continue the paper"
    )

    assert decision.domain == "chemistry"
    assert decision.research_target_level == "publishable"


def test_manager_without_backend_cannot_be_bypassed_by_vertical_env(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_VERTICAL", "math")
    with pytest.raises(VerticalDecisionError, match="no backend"):
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


def test_divide_commits_vertical_so_supervisor_trusts_it(tmp_path, optimize_vertical):
    mgr = Manager(project_root=tmp_path, runner=_existing(optimize_vertical))
    d = mgr.divide("maximize the pass-gap of the synthesized problem set")
    assert isinstance(d, Division)
    assert d.vertical == optimize_vertical
    # persisted into PIPELINE_STATE.json — the supervisor reads & trusts this
    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == optimize_vertical


def test_divide_persists_manager_owned_research_target(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    manager = Manager(project_root=tmp_path, runner=_existing("research"))

    division = manager.divide("verify this bounded lemma")

    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert division.vertical == "research"
    assert state["vertical"] == "research"
    assert state["research_target_level"] == "publishable"
    assert state["research_target_set_at"] > 0


def test_research_divide_persists_explicit_target_venue(tmp_path) -> None:
    runner = _DecisionRunner(
        {
            "choice": "existing",
            "vertical": "research",
            "workflow_mode": "staged",
            "execution_task": "write the requested paper",
            "research_target_level": "publishable",
            "target_venue": "AAAI",
        }
    )

    division = Manager(project_root=tmp_path, runner=runner).divide(
        "produce the requested AAAI paper"
    )

    state = json.loads(
        (tmp_path / ".argus" / "PIPELINE_STATE.json").read_text()
    )
    assert division.vertical == "research"
    assert state["target_venue"] == "AAAI"


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


def test_vertical_decision_can_be_committed_after_external_revision_check(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    mgr = Manager(project_root=tmp_path, runner=_existing("research"))
    agents_path = tmp_path / "AGENTS.md"
    original_agents = "# AGENTS.md\n\noperator-owned text\n"
    agents_path.write_text(original_agents, encoding="utf-8")

    decision = mgr.decide_vertical("draft the paper")

    assert not (tmp_path / ".argus" / "PIPELINE_STATE.json").exists()
    division = mgr.commit_vertical_decision("draft the paper", decision)
    assert division.execution_task == "perform the requested task"
    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"
    assert agents_path.read_text(encoding="utf-8") == original_agents


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


def test_failed_vertical_commit_restores_pipeline_state(tmp_path, monkeypatch, optimize_vertical):
    manager = Manager(project_root=tmp_path, runner=_existing("research"))
    manager.divide("seed the research pipeline")
    pipeline_state = tmp_path / ".argus" / "PIPELINE_STATE.json"
    before = pipeline_state.read_bytes()
    decision = VerticalDecision(
        choice="existing",
        vertical=optimize_vertical,
        execution_task="run the synthesis pipeline",
    )
    monkeypatch.setattr(
        "argus.manager._vertical_ops.vertical_select.reset_stage_for_new_intent",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reset failed")),
    )

    with pytest.raises(RuntimeError, match="reset failed"):
        manager.commit_vertical_decision("run the synthesis pipeline", decision)

    assert pipeline_state.read_bytes() == before


def test_divide_research_persists_and_lists_8_stages(tmp_path):
    d = Manager(project_root=tmp_path, runner=_existing("research")).divide(
        "draft a paper for EMNLP submission"
    )
    assert d.vertical == "research"
    assert d.stages == list(RESEARCH_STAGES)
    assert "vertical=research" in d.headline()
    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"


def test_root_task_id_scopes_manager_vertical_call(tmp_path):
    transitions: list[tuple[str, str]] = []

    @contextmanager
    def usage_context(root_task_id: str):
        transitions.append(("enter", root_task_id))
        try:
            yield
        finally:
            transitions.append(("exit", root_task_id))

    manager = Manager(
        project_root=tmp_path,
        runner=_existing("research"),
        usage_context=usage_context,
    )

    manager.divide("write a paper", root_task_id="root-task-1")

    assert transitions == [
        ("enter", "root-task-1"),
        ("exit", "root-task-1"),
    ]


def test_root_task_id_scopes_manager_front_door_call(tmp_path):
    transitions: list[tuple[str, str]] = []

    @contextmanager
    def usage_context(root_task_id: str):
        transitions.append(("enter", root_task_id))
        try:
            yield
        finally:
            transitions.append(("exit", root_task_id))

    manager = Manager(
        project_root=tmp_path,
        runner=_DecisionRunner({}),
        usage_context=usage_context,
    )

    manager.classify_front_door("build it", root_task_id="root-task-2")

    assert transitions == [
        ("enter", "root-task-2"),
        ("exit", "root-task-2"),
    ]


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


def test_grounded_vertical_decision_rewrites_task_without_unrequested_rendering(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "research",
        "execution_task": "Write the substantive manuscript.",
        "research_target_level": "publishable",
    })

    division = Manager(project_root=tmp_path, runner=runner).divide("write the paper")

    assert not (tmp_path / ".argus" / "live-view.json").exists()
    assert not (tmp_path / ".argus" / "live" / "current.md").exists()
    assert division.execution_task == "Write the substantive manuscript."
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-grounded"
    ]
    assert runner.last_options.sandbox_mode == "read-only"
    assert runner.last_options.force_safe_mode is True
    assert runner.last_options.dangerous_yolo is False


def test_vertical_decision_pins_manager_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "gpt-5.5")
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "gpt-5.6-sol")
    runner = _existing("direct")

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Fix one failing repository test and return the patch."
    )

    assert decision.vertical == "software"
    assert decision.workflow_mode == "direct"
    assert runner.last_options.model == "gpt-5.5"
    assert runner.calls[0]["options"].external_interrupt_reason_provider is None
    assert runner.calls[0]["options"].sandbox_mode == "read-only"
    assert "fast, tool-free front-door judgment" in runner.calls[0]["prompt"]


def test_custom_codex_provider_does_not_send_diagnostic_model_placeholder(
    tmp_path,
    monkeypatch,
) -> None:
    """`<backend default>` is a streak label, never a provider model id."""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
model_provider = "deepseek"
model = "deepseek-v4-flash"

[model_providers.deepseek]
base_url = "https://api.deepseek.invalid/"
wire_api = "responses"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.delenv("ARGUS_SKILL_MANAGER_MODEL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_MODEL", raising=False)
    runner = _existing("research")

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Write a short Chinese survey with a compiled PDF deliverable."
    )

    assert decision.vertical == "research"
    assert runner.last_options.model == ""
    assert runner.last_options.model != "<backend default>"


def test_argus_maintenance_skips_duplicate_manager_grounding_by_default(
    tmp_path,
) -> None:
    runner = _existing("argus_maintenance")

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Simplify Argus core while preserving recovery behavior."
    )

    assert decision.vertical == "argus_maintenance"
    assert "## Manager project grounding" not in decision.execution_task
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-fast"
    ]


def test_vertical_decision_always_uses_repository_grounded_route(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    class _GroundedRunner:
        _backend_name = "copilot"

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append({
                "prompt": prompt,
                "options": options,
                "run_label": run_label,
            })
            submit(options, route(**{
                "choice": "existing",
                "vertical": "software",
                "workflow_mode": "direct",
                "execution_task": (
                    "Repair the repository behavior while preserving its public API."
                ),
                "summary": "bounded repair after focused repository inspection",
            }))
            return _DecisionResult("")

    runner = _GroundedRunner()
    task = "Repair the repository behavior while preserving its public API."

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(task)

    assert decision.vertical == "software"
    assert decision.workflow_mode == "direct"
    assert decision.execution_task == task
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-grounded",
    ]
    assert runner.calls[0]["options"].sandbox_mode == "read-only"
    assert runner.calls[0]["options"].force_safe_mode is True
    assert runner.calls[0]["options"].dangerous_yolo is False
    assert "--available-tools=" not in runner.calls[0]["options"].extra_args
    assert 'inspect if the fit is unclear' in runner.calls[0]["prompt"]
    assert 'Preserve paths, commands, order' in runner.calls[0]["prompt"]
    assert 'omit it for a standalone existing route' in (
        runner.calls[0]["prompt"]
    )
    assert "at most one targeted" not in runner.calls[0]["prompt"]


def test_fast_route_carries_contract_fields_without_bypassing_planner(
    tmp_path,
) -> None:
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "software",
        "workflow_mode": "direct",
        "summary": "one bounded parser repair",
        "require_independent_review": True,
        "precise_constraints": ["pytest -q tests/test_parser.py exits zero"],
        "exclusions": ["do not change the public API"],
        "assumptions": [],
    })
    runner._backend_name = "codex"

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Repair the parser and run its focused test."
    )

    assert decision.workflow_mode == "direct"
    assert decision.require_independent_review is True
    assert decision.precise_constraints == (
        "pytest -q tests/test_parser.py exits zero",
    )
    assert decision.exclusions == ("do not change the public API",)
    assert decision.assumptions == ()
    assert runner.calls[0]["run_label"] == "manager-classify-fast"
    assert runner.calls[0]["options"].disable_tools is True
    assert runner.calls[0]["options"].extra_args == ["--ephemeral"]


def test_research_route_prompts_require_the_fields_the_parser_requires() -> None:
    shared = {
        "task": "Write a finite technical survey.",
        "verticals_with_purpose": {"research": "research and surveys"},
        "research_target_verticals": ("research",),
    }
    fast = build_fast_vertical_decision_prompt(**shared)
    grounded = build_vertical_decision_prompt(**shared)

    assert "always choose and output `research_target_level`" in fast
    assert "Always output `research_direction_mode`" in fast
    assert 'For research-target verticals, add `research_target_level`' in (
        grounded
    )
    assert "Add research target fields only when the operator stated them" not in fast
    assert '`target_venue` only if operator-stated' in grounded
    for prompt in (fast, grounded):
        assert 'Research figures, plots, diagrams, Figure 1' in prompt
        assert 'manuscript revisions are `research`' in prompt
        assert 'If only that part is requested and a full campaign excluded, choose `direct`' in prompt
        assert "Optional start_stage applies only to workflow_mode=direct" in prompt
        assert 'choose a stage of that vertical' in prompt
        assert 'leave empty for its first stage' in prompt
        assert 'Staged work always begins at its first stage' in prompt


@pytest.mark.parametrize("fast", [True, False], ids=["fast", "grounded"])
def test_direct_research_figure_route_seeds_paper(tmp_path, monkeypatch, fast) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "1" if fast else "0")
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "research",
        "workflow_mode": "direct",
        "start_stage": "paper",
        "research_target_level": "exploratory",
        "research_direction_mode": "locked",
    })
    manager = Manager(project_root=tmp_path, runner=runner)
    task = "Produce only publication figures for this paper; no full research campaign."

    decision = manager.decide_vertical(task)
    assert decision.start_stage == "paper"
    division = manager.commit_vertical_decision(task, decision)

    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert division.vertical == "research"
    assert division.workflow_mode == "direct"
    assert state["current_stage"] == "paper"
    assert runner.calls[0]["run_label"] == (
        "manager-classify-fast" if fast else "manager-classify-grounded"
    )


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


def test_fast_route_environment_cannot_restore_tool_free_shortcut(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "1")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE_MAX_PROMPT_CHARS", "1")
    runner = _existing("direct")

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Repair one bounded failing test."
    )

    assert decision.vertical == "software"
    assert decision.workflow_mode == "direct"
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-grounded",
    ]


def test_empty_workspace_builtin_research_does_not_require_ceremonial_tool_use(
    tmp_path,
) -> None:
    class _NoToolResearchRunner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append(run_label)
            submit(options, route(**{
                    "choice": "existing",
                    "vertical": "research",
                    "workflow_mode": "staged",
                    "execution_task": "Write the requested survey and compile its PDF.",
                    "summary": "explicit built-in research task in an empty workspace",
                    "research_target_level": "exploratory",
                }))
            return _DecisionResult("", tool_activity_observed=False)

    runner = _NoToolResearchRunner()
    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Write a Chinese survey and compile its PDF."
    )

    assert decision.vertical == "research"
    assert decision.workflow_mode == "staged"
    assert runner.calls == ["manager-classify-fast"]


def test_exploratory_research_keeps_manager_selected_staged_workflow(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "research",
        "workflow_mode": "staged",
        "execution_task": "Investigate the company from public sources.",
        "summary": "multiple public evidence tracks",
        "research_target_level": "exploratory",
    })

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Investigate Shanghai Qiadao Technology and verify any quantum claims."
    )

    assert decision.vertical == "research"
    assert decision.research_target_level == "exploratory"
    assert decision.target_venue == ""
    # Staged is an execution topology, not a publication claim. The exploratory
    # target keeps venue/idea bootstraps disabled while allowing dependent
    # evidence tracks to continue without another operator prompt.
    assert decision.workflow_mode == "staged"
    assert "Choose workflow separately" in runner.calls[0]["prompt"]
    assert "papers and surveys are `research`" in runner.calls[0]["prompt"]


def test_builtin_repository_route_accepts_host_snapshot_without_tool_retry(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    class _RetryRunner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append(run_label)
            submit(options, route(**{
                    "choice": "existing",
                    "vertical": "software",
                    "workflow_mode": "direct",
                    "execution_task": "Repair the repository.",
                    "summary": "Python repository repair",
                }))
            return _DecisionResult("", tool_activity_observed=False)

    runner = _RetryRunner()
    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        "Repair the repository."
    )

    assert decision.vertical == "software"
    assert runner.calls == ["manager-classify-grounded"]


def test_standalone_task_is_preserved_when_manager_omits_execution_task(
    tmp_path,
) -> None:
    task = "Repair the parser without changing tests."
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "software",
        "workflow_mode": "direct",
        "summary": "The standalone task is already a bounded software mission.",
    })

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(task)

    assert decision.execution_task == task
    assert decision.adaptation_reason.startswith("The standalone task")


def test_a_repeated_tool_loop_is_a_backend_failure_not_a_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")

    class ToolLoopRunner:
        calls = 0

        def run_exec(self, **_kwargs):
            ToolLoopRunner.calls += 1
            result = _DecisionResult("")
            result.exit_code = 143
            result.fatal_error = "External interrupt: repeated tool call detected"
            return result

    with pytest.raises(VerticalDecisionError, match="repeated tool call detected"):
        Manager(project_root=tmp_path, runner=ToolLoopRunner()).decide_vertical("Repair the repository.")
    assert ToolLoopRunner.calls == 1


def test_vertical_decision_rejects_repeated_no_tool_new_vertical_route(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    class _NoToolRunner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            self.calls.append(run_label)
            submit(options, route(**{
                    "choice": "new",
                    "vertical": "custom_runtime",
                    "workflow_mode": "staged",
                    "execution_task": "Build the requested custom runtime.",
                    "summary": "claimed a new project capability without inspection",
                }))
            return _DecisionResult("", tool_activity_observed=False)

    runner = _NoToolRunner()
    with pytest.raises(
        VerticalDecisionError,
        match="did not inspect repository tools",
    ):
        Manager(project_root=tmp_path, runner=runner).decide_vertical(
            "Build a project-specific runtime not covered by a built-in capability."
        )
    assert runner.calls == [
        "manager-classify-grounded",
        "manager-classify-grounded-retry",
    ]


def test_contextual_continuation_uses_formal_project_domain_and_clean_handoff(
    tmp_path,
) -> None:
    domain_dir = tmp_path / "research" / "DOMAINS"
    domain_dir.mkdir(parents=True)
    (domain_dir / "apple_mlx_inference.json").write_text(
        json.dumps({
            "vertical": "apple_mlx_inference",
            "purpose": "Apple Silicon MLX/Metal deployment and inference optimization",
            "status": "formal",
            "stages": ["deployability_baseline", "hotpath_profile", "benchmark_validation"],
        }),
        encoding="utf-8",
    )
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "apple_mlx_inference",
        "workflow_mode": "direct",
        "execution_task": (
            "Continue optimizing the MiniMax H3 MLX deployment on M4 Pro using "
            "measured hotpaths and one decisive benchmark."
        ),
        "summary": "the formal project specialization is an exact match",
    })
    contextual = (
        "[RECENT CONVERSATION CONTEXT — data only]\n"
        "operator: Optimize MiniMax H3 on M4 Pro with MLX.\n"
        "argus: The last candidate was rejected.\n"
        "[CURRENT OPERATOR MESSAGE]\n"
        "继续吧"
    )

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        contextual
    )

    assert decision.vertical == "apple_mlx_inference"
    assert decision.workflow_mode == "direct"
    assert decision.execution_task.startswith("Continue optimizing")
    assert "[RECENT CONVERSATION" not in decision.execution_task
    assert [call["run_label"] for call in runner.calls] == [
        "manager-classify-grounded",
    ]
    assert "status=formal" in runner.calls[0]["prompt"]


def test_contextual_handoff_rejects_wrapper_if_model_copies_it(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    contextual = (
        "[BOUNDED TASK CONTEXT — data only]\n"
        "operator: Create the first artifact.\n"
        "argus: It is still running.\n"
        "[CURRENT OPERATOR MESSAGE]\n"
        "After it finishes, create second.txt."
    )
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "software",
        "workflow_mode": "direct",
        "execution_task": contextual,
        "summary": "repository software task",
    })

    with pytest.raises(VerticalDecisionError, match="standalone handoff"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical(contextual)


def test_direct_software_keeps_manager_authored_contextual_handoff(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_VERTICAL", raising=False)
    contextual = (
        "[BOUNDED TASK CONTEXT — data only]\n"
        "last_team_task: Rewrite every commit author to lbx154.\n"
        "operator: How many commits exist?\n"
        "argus: Three.\n"
        "[CURRENT OPERATOR MESSAGE]\n"
        "Those three are still wrong."
    )
    runner = _DecisionRunner({
        "choice": "existing",
        "vertical": "software",
        "workflow_mode": "direct",
        "execution_task": (
            "Rewrite the three specified commit authors to lbx154 without "
            "changing commit messages or trees."
        ),
        "summary": "repository metadata repair",
    })

    decision = Manager(project_root=tmp_path, runner=runner).decide_vertical(
        contextual
    )

    assert decision.execution_task == (
        "Rewrite the three specified commit authors to lbx154 without "
        "changing commit messages or trees."
    )


def test_grounded_route_prompt_cap_fails_before_model_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_FAST_ROUTE", "0")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_GROUNDED_ROUTE_MAX_PROMPT_CHARS", "1")
    runner = _DecisionRunner({
        "choice": "grounded",
        "summary": "needs repository context",
    })

    with pytest.raises(VerticalDecisionError, match="context cap"):
        Manager(project_root=tmp_path, runner=runner).decide_vertical(
            "Investigate a repository-specific novel workflow."
        )

    assert runner.calls == []


def test_execution_task_is_string_only_and_lossless() -> None:
    actions = HandOffActions("route", check=RouteContext(known_verticals=("research",)).decide)
    payload = {"to": "team", "summary": "a paper", "assumptions": [], "choice": "existing", "vertical": "research",
               "research_target_level": "exploratory"}
    with pytest.raises(ValueError, match="execution_task"):
        actions.dispatch("hand_off", {**payload, "execution_task": {"bad": True}})
    actions.dispatch("hand_off", {**payload, "execution_task": "x" * 9000})
    assert actions.handoff.value.execution_task == "x" * 9000


def test_divide_resets_stage_when_new_intent_supersedes_finished_prior_vertical(tmp_path):
    """End-to-end regression for the vertical-resolution false-stage-advance
    bug: an OLD custom vertical (``ops_continuity_runbook``) already reached
    ITS OWN terminal stage ("review") with status="done". A brand-new,
    operator-issued Task now gets divided into the "research" vertical, whose
    8-stage order ALSO contains a stage literally named "review". Before the
    fix, ``Manager.divide`` would persist "research" but leave
    ``current_stage="review"`` untouched (``persist_vertical`` is seed-only),
    and since "review" is a valid member of research's own order,
    ``current_stage()`` would silently accept it as real progress on the
    brand-new project. After the fix, ``divide`` must reset ``current_stage``
    to research's FIRST stage.
    """
    from argus.skills.stage_machine import current_stage
    from argus.verticals._data_domain import write_data_domain

    old_stage_order = ("investigate", "configure", "dry_run", "document", "review")
    write_data_domain(
        tmp_path, "ops_continuity_runbook",
        stages=list(old_stage_order), checklist_stage_order=list(old_stage_order),
        created_by="manager",
    )
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": "ops_continuity_runbook",
            "current_stage": "review",
            "stages": {s: {"status": "done"} for s in old_stage_order},
        }),
        encoding="utf-8",
    )
    assert current_stage(tmp_path) == "review"  # old, unrelated project: done

    mgr = Manager(project_root=tmp_path, runner=_existing("research"))
    d = mgr.divide("write a brand new paper — totally unrelated to the old runbook")

    assert d.vertical == "research"
    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert state["vertical"] == "research"
    assert state["current_stage"] == "idea"
    assert current_stage(tmp_path) == "idea"


def test_divide_reopens_finished_pipeline_for_new_same_vertical_task(tmp_path):
    """Regression: a second research task must not immediately become planner done."""
    from argus.skills.vertical_select import vertical_reached_own_terminal_stage

    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "vertical": "research",
            "current_stage": "review",
            "stages": {stage: {"status": "done"} for stage in RESEARCH_STAGES},
        }),
        encoding="utf-8",
    )
    assert vertical_reached_own_terminal_stage(tmp_path, "research") is True

    division = Manager(
        project_root=tmp_path,
        runner=_existing("research"),
    ).divide("start a separate second research task")

    state = json.loads((tmp_path / ".argus" / "PIPELINE_STATE.json").read_text())
    assert division.vertical == "research"
    assert state["current_stage"] == "idea"
    assert state["research_intent_generation"] == 2
    assert vertical_reached_own_terminal_stage(tmp_path, "research") is False
