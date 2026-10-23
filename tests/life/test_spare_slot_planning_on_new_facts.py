"""Spare-slot planning beside a running mission asks only when a fact moved.

On 2026-10-15 a trial campaign called the Planner 111 times while a mission was
already running, and 104 of those answers were "still waiting": the parallel
worker re-asked on a timer for as long as the job ran. In the recorded
campaigns every re-ask that did schedule work came right after something the
Planner reads had changed, so the Planner is asked again on exactly that.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig, _core


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


def test_spare_slot_planner_is_asked_again_only_when_a_fact_moved(
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
    inputs = ["inputs-v1"]
    monkeypatch.setattr(
        supervisor, "_current_planner_input_signature", lambda: inputs[0]
    )
    asked: list[float] = []
    supervisor._plan_next_work = lambda **_kwargs: asked.append(clock[0])  # type: ignore[method-assign]

    running = BacklogItem.new(title="long training run", objective="train the model")
    supervisor.memory.backlog.add(running)
    supervisor.memory.backlog.mark_running(running.id)
    running_items = [running]

    # A long job with nothing new: one ask at the start, however long it runs.
    for now in (0.0, 300.0, 3600.0, 6 * 3600.0, 24 * 3600.0):
        clock[0] = now
        supervisor._plan_alongside_running_work(running_items)
    assert asked == [0.0]

    # Something the Planner reads moved (a mission settled, a job finished,
    # the operator wrote): ask once, then stay quiet on that state.
    inputs[0] = "inputs-v2"
    clock[0] = 25 * 3600.0
    supervisor._plan_alongside_running_work(running_items)
    clock[0] = 26 * 3600.0
    supervisor._plan_alongside_running_work(running_items)
    assert asked == [0.0, 25 * 3600.0]

    # New backlog state is new evidence too.
    supervisor.memory.backlog.add(
        BacklogItem.new(title="analysis that needs the result", objective="analyse")
    )
    clock[0] = 27 * 3600.0
    supervisor._plan_alongside_running_work(running_items)
    assert asked == [0.0, 25 * 3600.0, 27 * 3600.0]


def test_no_slot_is_filled_beside_work_that_does_not_share_its_slot(
    tmp_path: Path, monkeypatch
) -> None:
    """2026-10-22 (sizefmt): the Planner queued a task that was not
    parallel-safe; the second mission worker asked it again the moment that
    task started, and it answered "nothing new" for a slot nothing could run in."""
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    monkeypatch.setattr(
        "argus.daemon.state.read_continuous_state",
        lambda _root: SimpleNamespace(enabled=True, objective="add format_mib"),
    )
    monkeypatch.setattr(supervisor, "_current_planner_input_signature", lambda: "inputs")
    asked: list[bool] = []
    supervisor._plan_next_work = lambda **_kwargs: asked.append(True)  # type: ignore[method-assign]
    queued = BacklogItem.new(
        title="implement format_mib", objective="truncate like format_kib",
        owns_paths=["sizefmt/core.py", "tests/test_core.py"],
    )
    supervisor.memory.backlog.add(queued)
    supervisor.memory.backlog.mark_running(queued.id)

    supervisor.config.parallel_worker = True
    supervisor._plan_alongside_running_work([queued])
    assert asked == []
    # With one slot no claim waits on running work (a paused external job,
    # say), so the Planner can still fill the slot.
    supervisor.config.parallel_worker = False
    supervisor._plan_alongside_running_work([queued])
    assert asked == [True]
