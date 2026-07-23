"""Terminal empty-plan lifecycle regressions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus_skill.core.models import RunnerResult
from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor._config import LifeSupervisorConfig
from argus_skill.life.supervisor._constants import PLAN_ERROR, PLAN_TERMINAL_IDLE
from argus_skill.life.supervisor._core import LifeSupervisor


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _NullRunner:
    pass


class _EmptyPlannerThenManagerRunner:
    def __init__(self, *, manager_action: str = "hold") -> None:
        self.manager_action = manager_action
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
                "target_stage": "delivery",
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
