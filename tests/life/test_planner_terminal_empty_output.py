"""Terminal empty-plan lifecycle regressions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from argus_skill.core.event_catalog import EventType
from argus_skill.core.models import RunnerResult
from argus_skill.core.role_decision import encode_role_decision
from argus_skill.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
)
from argus_skill.life.memory import BacklogItem, LifeMemory, MemoryBundle
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    PLAN_AWAITING,
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.life.supervisor._core import LifeSupervisor
from argus_skill.manager import Manager
from argus_skill.planner import NO_CONCRETE_TASKS_ERROR, WORK_KINDS


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _NullRunner:
    pass


class _EmptyPlannerThenManagerRunner:
    def __init__(
        self,
        *,
        manager_action: str = "hold",
        manager_target_stage: str = "delivery",
    ) -> None:
        self.manager_action = manager_action
        self.manager_target_stage = manager_target_stage
        self.planner_calls = 0
        self.manager_calls = 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        if run_label.startswith("planner.cycle"):
            self.planner_calls += 1
            payload = "\n".join(
                [
                    "PROJECT_DONE=false",
                    "REASON=the final reviewer certification is already complete and "
                    "there is no legal follow-up work",
                ]
            )
        else:
            assert run_label == "manager-stage"
            self.manager_calls += 1
            payload = {
                "action": self.manager_action,
                "target_stage": self.manager_target_stage,
                "reason": "final delivery remains certified; hold terminal stage",
            }
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(payload) if isinstance(payload, dict) else payload],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=(
                "planner-thread"
                if run_label.startswith("planner.cycle")
                else None
            ),
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


class _ContentFilterPlannerRunner(_EmptyPlannerThenManagerRunner):
    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.planner_calls += 1
        return RunnerResult(
            exit_code=1,
            agent_messages=[],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=(
                "planner-thread"
                if run_label.startswith("planner.cycle")
                else None
            ),
            fatal_error=(
                "Copilot content filtering blocked the response; the identical "
                "prompt must not be retried"
            ),
            stop_kind="permanent_error",
        )


class _EmptyThenTaskPlannerRunner(_EmptyPlannerThenManagerRunner):
    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        if run_label.startswith("planner.cycle"):
            self.planner_calls += 1
            if self.planner_calls == 1:
                payload = "\n".join(
                    [
                        "PROJECT_DONE=false",
                        "REASON=the current repair is not complete yet",
                    ]
                )
            else:
                payload = "\n".join(
                    [
                        "PROJECT_DONE=false",
                        "REASON=repair retry produced concrete next work",
                        "TASK_KEY=planner-empty-repair",
                        "TASK_TITLE=Repair empty planner verdict handling",
                        (
                            "TASK_OBJECTIVE=Update planner lifecycle handling and "
                            "run the focused tests."
                        ),
                        "TASK_HYPOTHESIS=Empty verdict recovery loses concrete next work.",
                        "TASK_GOAL_CONTRIBUTION=Restore reliable Planner continuation.",
                        "TASK_EXPECTED_REGRESSIONS=Planner retry status may remain noisy during repair.",
                        "TASK_DECISION_RULE=Replan if the empty output comes from provider failure.",
                        "TASK_ACCEPTANCE_CHECK=pytest tests/planner/test_planner.py",
                        "TASK_SCOPE=bounded",
                        "TASK_STAGE_CLOSING=false",
                        "TASK_REQUIRE_INDEPENDENT_REVIEW=false",
                        "TASK_SKIP_STAGE_TRANSITION=false",
                    ]
                )
        else:
            assert run_label == "manager-stage"
            self.manager_calls += 1
            payload = {
                "action": self.manager_action,
                "target_stage": self.manager_target_stage,
                "reason": "final delivery remains certified; hold terminal stage",
            }
        return RunnerResult(
            exit_code=0,
            agent_messages=[json.dumps(payload) if isinstance(payload, dict) else payload],
            stdout_lines=[],
            stderr_lines=[],
            thread_id=(
                "planner-thread"
                if run_label.startswith("planner.cycle")
                else None
            ),
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


def _write_software_state(project: Path, *, done: bool) -> None:
    """Write a delivery stage that is genuinely certified when ``done``.

    ``status: done`` alone is not certification. `software` declares
    COMPLETION_CONTRACT_VERSION = 1, so the terminal reconciliation checks for a
    matching ``completion_contract_sha256`` — the guard that stops a prematurely
    "done" stage from reading as certified.

    This fixture used to write the status only, which made the "certified
    terminal" case it names unreachable. That mirrored production: until the
    completion livelock was fixed on 2026-07-26, a non-paper vertical could
    never obtain this certificate at all, so the scenario did not exist. Now
    that it does, the fixture builds it the way the Manager does.
    """
    research = project / "research"
    research.mkdir(parents=True, exist_ok=True)
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    record: dict = {"status": "done" if done else "in_progress"}
    state_path.write_text(
        json.dumps(
            {
                "vertical": "software",
                "current_stage": "delivery",
                "stages": {"delivery": record},
            }
        ),
        encoding="utf-8",
    )
    if not done:
        return
    from argus_skill.skills.stage_machine import completion_contract_fingerprint
    from argus_skill.verticals._base import (
        load_vertical,
        vertical_completion_contract_version,
    )

    version = vertical_completion_contract_version(
        load_vertical("software", project_root=project)
    )
    if version <= 0:
        return
    record["completion_contract_version"] = version
    record["completion_contract_sha256"] = completion_contract_fingerprint(
        project, "delivery", version=version
    )
    state_path.write_text(
        json.dumps(
            {
                "vertical": "software",
                "current_stage": "delivery",
                "stages": {"delivery": record},
            }
        ),
        encoding="utf-8",
    )


def _write_reviewed_math_scope_state(project: Path) -> None:
    research = project / "research"
    research.mkdir(parents=True, exist_ok=True)
    state_path = project / ".argus" / "PIPELINE_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "vertical": "math",
                "current_stage": "scope",
                "research_target_level": "doctoral",
                "workflow_mode": "staged",
                "math_objective_mode": "exploratory",
            }
        ),
        encoding="utf-8",
    )
    (research / "scope_definition.json").write_text(
        json.dumps(
            {
                "artifact_kind": "scope_definition",
                "stage": "scope",
                "research_target_level": "doctoral",
                "candidate_screening_started": False,
                "reviewer_decisive_check": {
                    "scope.problem_explicit": "satisfied",
                    "scope.success_criterion": "satisfied",
                },
            }
        ),
        encoding="utf-8",
    )


def _candidate_artifact_paths(project: Path) -> list[Path]:
    research = project / "research"
    return [path for path in research.rglob("*candidate*") if path.name != "scope_definition.json"]


def _make_supervisor(
    tmp_path: Path,
    monkeypatch,
    *,
    terminal_stage_done: bool,
    split_memory: bool = False,
    backend: _EmptyPlannerThenManagerRunner | None = None,
) -> tuple[LifeSupervisor, _EmptyPlannerThenManagerRunner, _RecordingSink]:
    project = tmp_path / "project"
    project.mkdir()
    _write_software_state(project, done=terminal_stage_done)
    if split_memory:
        memory = MemoryBundle.for_cwd(
            project,
            global_root=tmp_path / "global",
            fingerprint="s-empty-plan",
        )
        memory.global_mem.init()
        memory.project.init()
    else:
        memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    backend = backend or _EmptyPlannerThenManagerRunner()
    runner = _NullRunner()
    runner.manager = Manager(
        project_root=project,
        execution_workdir=project,
        runner=backend,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="finish the private framework repair",
            paper_mission=False,
            final_certification_gate=False,
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=backend,
    )

    monkeypatch.setattr(supervisor, "_maybe_idle_after_unchanged_open_ended_done", lambda: None)
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None)
    monkeypatch.setattr(supervisor, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(supervisor, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_recent_subagent_family_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_effective_final_certification_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(supervisor, "_planner_runtime_with_idle_note", lambda: "")
    return supervisor, backend, sink


def test_bounded_completed_campaign_stops_before_planner_cycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )
    supervisor.config.open_ended = False
    monkeypatch.setattr(
        supervisor,
        "_plan_next_work",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("completed bounded campaign must not enter Planner")
        ),
    )

    result = supervisor.run()

    assert result["stopped_by"] == "project_done"
    assert result["planning_cycles"] == 0
    assert not any(
        event.get("type") == EventType.LIFE_PLANNER_START
        for event in sink.events
    )


def test_standing_campaign_is_not_stopped_by_bounded_completion_certificate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )
    planner_calls = 0

    def plan_once(*args, **kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return PLAN_TERMINAL_IDLE

    monkeypatch.setattr(supervisor, "_plan_next_work", plan_once)

    result = supervisor.run()

    assert result["stopped_by"] == PLAN_TERMINAL_IDLE
    assert planner_calls == 1


def test_content_filtered_planner_disarms_campaign_instead_of_retrying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.daemon.state import (
        read_continuous_state,
        write_continuous_config,
    )

    backend = _ContentFilterPlannerRunner()
    supervisor, _backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=backend,
    )
    write_continuous_config(
        supervisor.memory.root,
        enabled=True,
        objective="standing filtered campaign",
    )

    assert supervisor._plan_next_work() == PLAN_ERROR

    state = read_continuous_state(supervisor.memory.root)
    assert state.enabled is False
    assert "operator reformulation required" in state.done_reason
    assert backend.planner_calls == 1
    error = next(event for event in sink.events if event.get("type") == "life.planner.error")
    assert error["operator_alert"] is True
    assert error["stop_kind"] == "permanent_error"


def test_certified_terminal_empty_plan_completes_without_planner_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )

    assert supervisor._plan_next_work() == PLAN_TERMINAL_IDLE

    assert backend.planner_calls == 2
    assert backend.manager_calls == 1
    assert supervisor.memory.backlog.pending() == []
    assert not any(event.get("type") == "life.planner.error" for event in sink.events)
    planner_verdicts = [
        event for event in sink.events if event.get("type") == "life.planner.verdict"
    ]
    assert len(planner_verdicts) == 1
    assert planner_verdicts[0]["status"] == "completed"
    assert planner_verdicts[0]["completion_kind"] == "terminal_stage_hold"
    # `complete` since 2026-07-26: a non-paper vertical could not previously
    # obtain a completion decision at all, so this reconciliation only ever saw
    # `hold`. Both mean "the pipeline is at its end and is not going backwards".
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") in {"hold", "complete"}
        and event.get("trigger") == "open_ended_terminal_stage_reconciliation"
        for event in sink.events
    )


def test_nonterminal_empty_plan_repairs_into_concrete_backlog_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=_EmptyThenTaskPlannerRunner(),
    )

    assert supervisor._plan_next_work() is True

    assert backend.planner_calls == 2
    assert backend.manager_calls == 0
    pending = supervisor.memory.backlog.pending()
    assert len(pending) == 1
    assert pending[0].title == "Repair empty planner verdict handling"
    assert pending[0].acceptance_check == "pytest tests/planner/test_planner.py"
    assert not any(event.get("type") == "life.planner.error" for event in sink.events)
    assert any(
        event.get("type") == "life.planner.task_added"
        and event.get("title") == "Repair empty planner verdict handling"
        for event in sink.events
    )


def test_nonterminal_empty_plan_repair_exhaustion_asks_operator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )
    set_active_manager_directive(
        supervisor.memory.root,
        "questions are allowed",
        operator_question_policy="allow",
    )

    assert supervisor._plan_next_work() == PLAN_AWAITING

    assert backend.planner_calls == 2
    assert backend.manager_calls == 0
    paused = [
        item for item in supervisor.memory.backlog.all()
        if item.status == "paused_operator"
    ]
    assert len(paused) == 1
    assert "cannot identify a concrete next task" in paused[0].pending_question
    error_event = next(
        event for event in sink.events if event.get("type") == "life.planner.error"
    )
    assert str(error_event.get("error", "")).startswith(NO_CONCRETE_TASKS_ERROR)
    assert "repair exhausted after 1 attempt" in str(error_event.get("error", ""))
    assert error_event["operator_alert"] is True
    assert any(
        event.get("type") == "life.operator_question.pending"
        for event in sink.events
    )
    assert not any(
        event.get("type") == "life.planner.verdict" and event.get("status") == "completed"
        for event in sink.events
    )


def test_nonterminal_empty_plan_does_not_park_when_questions_are_forbidden(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.manager.directive import set_active_manager_directive

    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )
    set_active_manager_directive(
        supervisor.memory.root,
        "continue without questions",
        operator_question_policy="forbid",
    )

    assert supervisor._plan_next_work() == PLAN_ERROR

    assert backend.planner_calls == 2
    assert backend.manager_calls == 0
    assert supervisor.memory.backlog.all() == []
    assert not any(
        event.get("type") == "life.operator_question.pending"
        for event in sink.events
    )
    error_event = next(
        event for event in sink.events if event.get("type") == "life.planner.error"
    )
    assert str(error_event.get("error", "")).startswith(NO_CONCRETE_TASKS_ERROR)
    assert "repair exhausted after 1 attempt" in str(error_event.get("error", ""))
    assert error_event["operator_alert"] is False
    assert error_event["recoverable"] is True
    assert error_event["stop_kind"] == "planner_empty_plan"


class _StructuredWorkKindPlannerRunner(_EmptyPlannerThenManagerRunner):
    def __init__(self, work_kind: str) -> None:
        super().__init__()
        self.work_kind = work_kind

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label.startswith("planner.cycle")
        self.planner_calls += 1
        payload = {
            "project_done": False,
            "reason": "one explicitly typed task remains",
            "tasks": [{
                "key": "typed-task",
                "deps": [],
                "title": "Execute typed work",
                "objective": "Run the task exactly as structured.",
                "scope": "bounded",
                "work_kind": self.work_kind,
            }],
        }
        return RunnerResult(
            exit_code=0,
            agent_messages=[encode_role_decision("planner", payload)],
            stdout_lines=[],
            stderr_lines=[],
            thread_id="planner-thread",
        )


@pytest.mark.parametrize("work_kind", WORK_KINDS)
def test_structured_work_kind_persists_and_reaches_engineer_context(
    tmp_path: Path,
    monkeypatch,
    work_kind: str,
) -> None:
    supervisor, backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=_StructuredWorkKindPlannerRunner(work_kind),
    )

    assert supervisor._plan_next_work() is True

    assert backend.planner_calls == 1
    pending = supervisor.memory.backlog.pending()
    assert len(pending) == 1
    item = pending[0]
    assert item.work_kind == work_kind
    assert f"- work_kind: {work_kind}" in supervisor._build_mission_prelude(item)


def test_legacy_backlog_item_keeps_generic_work_kind_without_prose_inference() -> None:
    item = BacklogItem.from_jsonable({
        "id": "legacy-item",
        "ts": 1.0,
        "title": "Deliver optimized algorithm",
        "objective": "Set up the environment, validate, and ship.",
    })

    assert item.work_kind == ""


def test_nonterminal_empty_plan_repair_exhaustion_stops_for_operator_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )

    summary = supervisor.run()

    assert summary["stopped_by"] == PLAN_AWAITING
    assert backend.planner_calls == 2
    error_event = next(
        event for event in sink.events if event.get("type") == "life.planner.error"
    )
    assert str(error_event.get("error", "")).startswith(NO_CONCRETE_TASKS_ERROR)
    assert "repair exhausted after 1 attempt" in str(error_event.get("error", ""))


def test_nonterminal_planning_replays_unassessed_current_stage_review_first(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    backend.manager_action = "advance"
    backend.manager_target_stage = "solve"
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_math_scope_state(project)
    assert _candidate_artifact_paths(project) == []
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded"],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The scope checklist is satisfied by the current artifacts.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    assert not (supervisor.memory.root / "handoffs").exists()

    assert supervisor._plan_next_work() == PLAN_RETRY

    assert backend.planner_calls == 0
    assert backend.manager_calls == 1
    state = json.loads((project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "solve"
    assert state["research_target_level"] == "doctoral"
    assert _candidate_artifact_paths(project) == []
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.outcome["stage_certification"] == "certified"
    assert supervisor.memory.backlog.pending() == []
    assert not any(event.get("type") == "life.planner.error" for event in sink.events)
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "advance"
        and event.get("trigger") == "reviewed_stage_empty_plan_reconciliation"
        and event.get("recovered_item_id") == item.id
        for event in sink.events
    )


def test_newer_replan_review_blocks_older_stage_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = _EmptyThenTaskPlannerRunner()
    supervisor, backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        backend=backend,
        split_memory=True,
    )
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_math_scope_state(project)
    older = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded", "stage:scope"],
        )
    )
    older_mission = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=older.id,
        stage="scope",
        objective=older.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=older_mission,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The previous scope evidence passed.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        older.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )
    newer = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Challenge the mathematical scope",
            objective="Test whether the accepted scope evidence remains valid.",
            tags=["planner", "scope:bounded", "stage:scope"],
        )
    )
    newer_mission = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=newer.id,
        stage="scope",
        objective=newer.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=newer_mission,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="continue",
            reason="The newer evidence invalidates the previous scope decision.",
            next_action="Repair the scope evidence.",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.update(
        newer.id,
        status="failed",
        finished_ts=time.time() + 1,
        outcome={
            "execution_status": "ended",
            "review_status": "continue",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert supervisor._plan_next_work() is True

    assert backend.planner_calls == 2
    assert backend.manager_calls == 0
    state = json.loads((project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "scope"


def test_bounded_continuous_campaign_replays_deferred_stage_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A bounded staged campaign still traverses stages.

    ``open_ended`` decides whether a Planner ``project_done`` is honoured, not
    whether stages may advance; gating the replay on it meant no vertical whose
    completion gate is not ``certified`` could ever leave its first stage. The
    item carries ``stage_certification="deferred"`` — a Planner node that held
    the stage rather than closing it — which is precisely the reviewed evidence
    this replay exists to recover.
    """
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    supervisor.config.open_ended = False
    backend.manager_action = "advance"
    backend.manager_target_stage = "solve"
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_math_scope_state(project)
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Define the mathematical scope",
            objective="State the admissible conjecture class and completion bar.",
            tags=["planner", "scope:bounded", "bounded_dag_node"],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The scope checklist is satisfied by the current artifacts.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "deferred",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert supervisor._plan_next_work() == PLAN_RETRY

    state = json.loads((project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
    assert state["current_stage"] == "solve"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.outcome["stage_certification"] == "certified"
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "advance"
        and event.get("trigger") == "reviewed_stage_empty_plan_reconciliation"
        and event.get("recovered_item_id") == item.id
        for event in sink.events
    )


def test_review_only_item_is_never_replayed_into_stage_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, _backend, _sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
        split_memory=True,
    )
    project = Path(supervisor.config.project_worktree)
    _write_reviewed_math_scope_state(project)
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Review bounded candidate",
            objective="Assess a candidate without changing the formal stage.",
            tags=[
                "planner",
                "scope:bounded",
                "review:required",
                "stage_transition:skip",
            ],
        )
    )
    mission_path = create_mission_context(
        life_dir=supervisor.memory.project_root,
        mission_id=item.id,
        stage="scope",
        objective=item.objective,
        scope="bounded",
    )
    record_reviewed_handoff(
        mission_context_path=mission_path,
        round_index=1,
        engineer_summary="",
        review=SimpleNamespace(
            status="done",
            reason="The bounded candidate review is complete.",
            next_action="",
            operator_question="",
        ),
        checkpoint_path=None,
    )
    supervisor.memory.backlog.mark_done(
        item.id,
        outcome={
            "execution_status": "completed",
            "review_status": "done",
            "stage_certification": "not_assessed",
            "interruption_kind": "none",
            "resumable": False,
        },
    )

    assert supervisor._latest_unassessed_review_for_current_stage() is None
    state = json.loads(
        (project / ".argus" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert state["current_stage"] == "scope"


def test_replan_with_no_planner_tasks_reaches_the_manager(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A replan the Planner cannot turn into tasks must not dead-end.

    Observed live on a real project: the Reviewer returns ``replan_requested``
    ("integrity-complete, but this direction cannot meet the bar inside the
    frozen boundary"), the item goes back to pending, and the Planner agrees —
    structurally answering "not done, and I have no task to propose". The
    reconciliation that exists for exactly that answer was skipped whenever a
    revision was in flight, so the verdict became a plain planner error, the
    cycle backed off, the pending item was claimed again, and the same mission
    reran. That project did it 100 times across 75 hours without ever changing
    course.

    The Planner has to be able to get itself out rather than the operator being
    paged, and the way out is a stage decision: its verdict must reach the
    Manager, the sole stage authority, so a rollback makes earlier-stage work
    enqueueable on the next cycle. This asserts the verdict gets there; what the
    Manager then decides is the Manager's business.
    """
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=True,
    )
    reconciled: list[Any] = []

    def _record(verdict: Any) -> str:
        reconciled.append(verdict)
        return "rollback"

    monkeypatch.setattr(
        supervisor, "_reconcile_open_ended_terminal_stage_action", _record
    )

    # A real replan arrives from a versioned, still-active backlog item — the
    # one the Reviewer just judged.
    item = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="Validating frozen held-out predictions",
        objective="Validating frozen held-out predictions",
        status="running",
        plan_id="plan-1",
        plan_version=1,
    )
    supervisor.memory.backlog.add(item)

    outcome = supervisor._plan_next_work(
        revision_request={
            "item_id": item.id,
            "expected_plan_id": "plan-1",
            "expected_plan_version": 1,
            "review_reason": (
                "integrity-complete but the direction cannot meet the bar "
                "inside the frozen boundary"
            ),
        }
    )

    assert reconciled, "the Planner's no-task verdict never reached the Manager"
    assert outcome == PLAN_RETRY, "a rolled-back stage must give the Planner another cycle"
    assert outcome != PLAN_ERROR


def test_unversioned_item_replan_degrades_to_planning_not_a_dead_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """An item predating plan versioning must not make replanning impossible.

    The revision path compare-and-swaps an active plan. An item with no
    ``plan_id`` has no plan to swap, and the intake used to answer that with
    ``PLAN_ERROR``: the Reviewer's replan is discarded, the cycle backs off, the
    item is claimed again, and the same mission reruns. Being unversioned is not
    a condition that resolves, so that loop has no exit — the same shape as the
    replan deadlock above, reached by a different door. One such item is live on
    this host.

    With nothing to supersede, the honest degradation is an ordinary planning
    cycle: the Planner still gets to decide what happens next.
    """
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )

    legacy = BacklogItem(
        id=BacklogItem.new_id(),
        ts=time.time(),
        title="恢复 Erdős 52 任意 n frontier 研究",
        objective="resume the frontier search",
        status="running",
    )
    supervisor.memory.backlog.add(legacy)
    assert not legacy.plan_id

    supervisor._plan_next_work(
        revision_request={
            "item_id": legacy.id,
            "review_reason": "this direction is exhausted",
        }
    )

    # The exit from the loop is that the Planner gets invoked at all: before,
    # intake returned PLAN_ERROR without ever calling it, and "unversioned" never
    # stops being true. This stub Planner then returns no tasks, so the cycle
    # still ends in an error — that is the stub, not the dead end.
    assert backend.planner_calls >= 1, "the Planner never got to decide"
    rejected = [
        event
        for event in sink.events
        if event.get("type") == "life.plan.revision.rejected"
    ]
    assert rejected, "the degradation must stay on the record"
    assert "planning fresh work instead" in rejected[0]["reason"]
