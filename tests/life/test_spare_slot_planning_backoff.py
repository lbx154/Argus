"""Spare-slot planning beside a running mission must not re-ask on a flat timer.

On 2026-10-15 a trial campaign called the Planner 111 times while a mission was
already running, and 104 of those answers were "still waiting": the parallel
worker re-asked every idle-cap interval for as long as the job ran. The retry
interval now doubles while the backlog is unchanged and resets when it moves.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig, _core
from argus.life.supervisor._constants import (
    IDLE_BACKOFF_CAP_SECONDS,
    PARALLEL_PLAN_INTERVAL_CAP_SECONDS,
)


def _supervisor(project: Path, life: Path) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    return LifeSupervisor(
        memory=memory,
        runner=object(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            poll_interval_seconds=0.01,
            project_worktree=project,
            artifact_root=project,
        ),
        planner_runner=object(),
    )


def test_unchanged_backlog_doubles_the_spare_slot_interval(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    monkeypatch.setattr(
        "argus.daemon.state.read_continuous_state",
        lambda _root: SimpleNamespace(enabled=True, objective="finish the study"),
    )
    clock = [0.0]
    monkeypatch.setattr(_core.time, "monotonic", lambda: clock[0])
    asked: list[float] = []
    supervisor._plan_next_work = lambda **_kwargs: asked.append(clock[0])  # type: ignore[method-assign]

    running = BacklogItem.new(title="long training run", objective="train the model")
    supervisor.memory.backlog.add(running)
    supervisor.memory.backlog.mark_running(running.id)
    running_items = [running]

    base = IDLE_BACKOFF_CAP_SECONDS
    for now in (0.0, base - 1, base, 3 * base - 1, 3 * base):
        clock[0] = now
        supervisor._plan_alongside_running_work(running_items)
    assert asked == [0.0, base, 3 * base], "each unchanged ask waits twice as long"

    # New backlog state is new evidence: ask now and restart from the base.
    supervisor.memory.backlog.add(
        BacklogItem.new(title="analysis that needs the result", objective="analyse")
    )
    clock[0] = 3 * base + 1
    supervisor._plan_alongside_running_work(running_items)
    clock[0] = 4 * base
    supervisor._plan_alongside_running_work(running_items)
    assert asked[3:] == [3 * base + 1]
    assert supervisor._parallel_plan_interval == base

    for _ in range(20):
        clock[0] = supervisor._parallel_plan_after
        supervisor._plan_alongside_running_work(running_items)
    assert supervisor._parallel_plan_interval == PARALLEL_PLAN_INTERVAL_CAP_SECONDS
