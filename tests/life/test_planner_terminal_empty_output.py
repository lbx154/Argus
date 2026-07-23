"""Terminal empty-plan lifecycle regressions."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from argus_skill.core.models import RunnerResult
from argus_skill.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
)
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._constants import (
    PLAN_ERROR,
    PLAN_RETRY,
    PLAN_TERMINAL_IDLE,
)
from argus_skill.life.supervisor._core import LifeSupervisor


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
            payload = {
                "project_done": False,
                "reason": (
                    "the final reviewer certification is already complete and "
                    "there is no legal follow-up work"
                ),
                "waiting": False,
                "waiting_reason": "",
                "new_tasks": [],
            }
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
            agent_messages=[json.dumps(payload)],
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
                "stages": {
                    "delivery": {"status": "done" if done else "in_progress"}
                },
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
    return [
        path for path in research.rglob("*candidate*")
        if path.name != "scope_definition.json"
    ]


def _make_supervisor(
    tmp_path: Path,
    monkeypatch,
    *,
    terminal_stage_done: bool,
) -> tuple[LifeSupervisor, _EmptyPlannerThenManagerRunner, _RecordingSink]:
    project = tmp_path / "project"
    project.mkdir()
    _write_software_state(project, done=terminal_stage_done)
    memory = LifeMemory.open(tmp_path / "life")
    sink = _RecordingSink()
    backend = _EmptyPlannerThenManagerRunner()
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

    monkeypatch.setattr(
        supervisor, "_maybe_idle_after_unchanged_open_ended_done", lambda: None
    )
    monkeypatch.setattr(supervisor, "_resolve_vertical_once", lambda: None)
    monkeypatch.setattr(
        supervisor, "_wiki_collect_task_if_due_under_blocker", lambda: None
    )
    monkeypatch.setattr(supervisor, "_render_journal_for_planner", lambda: "")
    monkeypatch.setattr(supervisor, "_recent_no_progress_failures", lambda: {})
    monkeypatch.setattr(supervisor, "_recent_subagent_family_failures", lambda: {})
    monkeypatch.setattr(
        supervisor, "_effective_full_paper_gate", lambda *_a, **_k: False
    )
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

    assert backend.planner_calls == 1
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


def test_nonterminal_empty_plan_still_fails_with_planner_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
    )

    assert supervisor._plan_next_work() == PLAN_ERROR

    assert backend.planner_calls == 1
    assert backend.manager_calls == 0
    assert supervisor.memory.backlog.pending() == []
    assert any(event.get("type") == "life.planner.error" for event in sink.events)
    assert not any(
        event.get("type") == "life.planner.verdict"
        and event.get("status") == "completed"
        for event in sink.events
    )


def test_nonterminal_empty_plan_replays_unassessed_current_stage_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    supervisor, backend, sink = _make_supervisor(
        tmp_path,
        monkeypatch,
        terminal_stage_done=False,
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
        life_dir=supervisor.memory.root,
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

    assert supervisor._plan_next_work() == PLAN_RETRY

    assert backend.planner_calls == 1
    assert backend.manager_calls == 1
    state = json.loads(
        (project / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
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
