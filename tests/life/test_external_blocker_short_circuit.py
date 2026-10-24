"""External-blocker artifact discovery -- generic glob, no dated filenames."""
from __future__ import annotations

import json
from pathlib import Path

from argus.life.supervisor._helpers import (
    _operator_only_external_blocker_wait_reason_for_project,
)


def _write_blocker(project_root: Path, filename: str, payload: dict) -> Path:
    diagnosis = project_root / "diagnosis"
    diagnosis.mkdir(parents=True, exist_ok=True)
    path = diagnosis / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_plan_next_work_short_circuits_before_planner_runner(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    _write_blocker(
        project,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )

    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    mem = LifeMemory.open(tmp_path / "life")
    mem.init()
    events: list[dict] = []

    class _Sink:
        def handle_event(self, event: dict) -> None:
            events.append(event)

    class _Runner:
        pass

    class _PlannerRunnerThatMustNotBeCalled:
        def run_exec(self, **_kwargs):  # pragma: no cover - proves no call
            raise AssertionError("planner runner should not be called")

    from argus.life.event_log import JsonlEventSink
    from argus.skills.vertical_select import persist_vertical

    sup = LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=JsonlEventSink(_Sink(), life_dir=mem.root),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            continuous=True,
            continuous_objective="bounded survey",
            paper_mission=True,
            final_certification_gate=True,
        ),
        planner_runner=_PlannerRunnerThatMustNotBeCalled(),
    )

    # Mirror daemon boot: the vertical is resolved + persisted before the
    # supervisor loop runs, so the first cycle sees a research-gated vertical
    # and the operator-external-blocker short-circuit fires (fail-hard resolve
    # keeps the gate False until a vertical is persisted). The capturing sink is
    # teed through JsonlEventSink so mem.journal (an EventJournal over
    # events.jsonl) sees the emitted life.planner.idle event.
    persist_vertical(sup._artifact_root(), "research")

    result = sup._plan_next_work()

    assert result == "awaiting_external"
    assert any(e.get("type") == "life.planner.idle" for e in events)
    assert "planner_idle" in [entry.kind for entry in mem.journal.tail(5)]


def test_blocker_resolves_when_all_targets_present(tmp_path: Path):
    _write_blocker(
        tmp_path,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/a.csv", "data/eval/b.csv"],
        },
    )
    (tmp_path / "data" / "eval").mkdir(parents=True)
    (tmp_path / "data" / "eval" / "a.csv").write_text("ok\n", encoding="utf-8")
    (tmp_path / "data" / "eval" / "b.csv").write_text("ok\n", encoding="utf-8")

    assert _operator_only_external_blocker_wait_reason_for_project(tmp_path) == ""


def test_malformed_blocker_json_is_treated_as_waiting(tmp_path: Path):
    diagnosis = tmp_path / "diagnosis"
    diagnosis.mkdir()
    (diagnosis / "operator_only_external_blocker_x.json").write_text("{", encoding="utf-8")

    reason = _operator_only_external_blocker_wait_reason_for_project(tmp_path)

    assert reason
    assert "malformed JSON" in reason


def test_bounded_mission_does_not_short_circuit_on_external_blocker(tmp_path: Path):
    """Regression: with ``final_certification_gate=False`` (a ``--bounded`` mission), an
    operator-only external blocker must NOT short-circuit the planner cycle.

    Otherwise a bounded diagnostic/survey mission can never reach
    ``project_done`` and waits forever on external artifacts it does not need.
    Here, with the short-circuit correctly bypassed and no planner runner
    wired, ``_plan_next_work`` falls through to the "no planner runner" path
    instead of emitting an ``awaiting_external`` waiting verdict.
    """
    project = tmp_path / "project"
    project.mkdir()
    _write_blocker(
        project,
        "operator_only_external_blocker_20260605.json",
        {
            "local_engineer_action_required_before_mount": False,
            "required_external_targets": ["data/eval/wise.csv"],
            "canonical_viability_verdict": "blocked: data missing",
            "next_owner": "operator",
        },
    )

    from argus.life.memory import LifeMemory
    from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

    mem = LifeMemory.open(tmp_path / "life")
    mem.init()
    events: list[dict] = []

    class _Sink:
        def handle_event(self, event: dict) -> None:
            events.append(event)

    class _Runner:
        pass

    sup = LifeSupervisor(
        memory=mem,
        runner=_Runner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            continuous=True,
            continuous_objective="bounded survey",
            final_certification_gate=False,
        ),
        planner_runner=None,
    )

    result = sup._plan_next_work()

    # Short-circuit bypassed: NOT awaiting_external / planner_waiting.
    assert result != "awaiting_external"
    assert not any(e.get("type") == "life.planner.idle" for e in events)
    tail = mem.journal.tail(1)
    assert not tail or tail[0].kind != "planner_waiting"
