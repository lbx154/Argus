"""Terminal empty-plan lifecycle regressions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from argus_skill.core.models import RunnerResult
from argus_skill.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
)
from argus_skill.life.memory import BacklogItem, LifeMemory, MemoryBundle
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.life.supervisor._core import LifeSupervisor
from argus_skill.planner import NO_CONCRETE_TASKS_ERROR


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
            thread_id=None,
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
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
                        "TASK_ACCEPTANCE_CHECK=pytest tests/planner/test_planner.py",
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
            thread_id=None,
            fatal_error=None,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )


def _write_software_state(project: Path, *, done: bool) -> None:
    (project / "research").mkdir(parents=True)
    (project / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps(
            {
                "vertical": "software",
                "current_stage": "delivery",
                "stages": {"delivery": {"status": "done" if done else "in_progress"}},
            }
        ),
        encoding="utf-8",
    )


def _write_reviewed_math_scope_state(project: Path) -> None:
    research = project / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "PIPELINE_STATE.json").write_text(
        json.dumps(
            {
                "vertical": "math",
                "current_stage": "scope",
                "research_target_level": "doctoral",
                "workflow_mode": "staged",
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
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_NullRunner(),
        sink=sink,
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="finish the private framework repair",
            paper_mission=False,
            full_paper_gate=False,
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
    monkeypatch.setattr(supervisor, "_effective_full_paper_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(supervisor, "_planner_runtime_with_idle_note", lambda: "")
    return supervisor, backend, sink


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
    assert any(
        event.get("type") == "life.manager.stage_decision"
        and event.get("action") == "hold"
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


def test_nonterminal_empty_plan_repair_exhaustion_fails_with_planner_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )

    assert supervisor._plan_next_work() == PLAN_ERROR

    assert backend.planner_calls == 2
    assert backend.manager_calls == 0
    assert supervisor.memory.backlog.pending() == []
    error_event = next(
        event for event in sink.events if event.get("type") == "life.planner.error"
    )
    assert str(error_event.get("error", "")).startswith(NO_CONCRETE_TASKS_ERROR)
    assert "repair exhausted after 1 attempt" in str(error_event.get("error", ""))
    assert not any(
        event.get("type") == "life.planner.verdict" and event.get("status") == "completed"
        for event in sink.events
    )


def test_nonterminal_empty_plan_repair_exhaustion_stops_run_with_planner_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )

    summary = supervisor.run()

    assert summary["stopped_by"] == "planner_error"
    assert backend.planner_calls == 2
    error_event = next(
        event for event in sink.events if event.get("type") == "life.planner.error"
    )
    assert str(error_event.get("error", "")).startswith(NO_CONCRETE_TASKS_ERROR)
    assert "repair exhausted after 1 attempt" in str(error_event.get("error", ""))


def test_nonterminal_empty_plan_replays_unassessed_current_stage_review(
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

    assert backend.planner_calls == 2
    assert backend.manager_calls == 1
    state = json.loads((project / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8"))
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
        (project / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
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
