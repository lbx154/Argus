"""The Planner sizes work against a clock it can see.

A Planner in a one-week window scheduled about six weeks of runs: the digest
never told it how much time was left or how long finished runs had actually
taken. The time block is information only; whether the plan fits stays the
Planner's judgement.
"""

from __future__ import annotations

import time
from pathlib import Path

from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
from argus.skills.vertical_select import persist_vertical


def _supervisor(project: Path, life: Path) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=object(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="train and evaluate",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
        ),
    )
    persist_vertical(project, "software", workflow_mode="direct")
    return supervisor


def _finished(backlog, title: str, hours: float) -> None:
    item = BacklogItem.new(title=title, objective=f"objective for {title}")
    backlog.add(item)
    now = time.time()
    backlog.update(
        item.id, status="done", started_ts=now - 7200 - hours * 3600,
        finished_ts=now - 7200,
    )


def test_digest_shows_time_block_with_deadline_and_measured_runs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    state = read_pipeline_state(project)
    state["deadline"] = time.time() + 30 * 3600
    write_pipeline_state(project, state)
    backlog = supervisor.memory.backlog
    for title, hours in (("run a", 2.0), ("run b", 4.0), ("run c", 6.0)):
        _finished(backlog, title, hours)
    running = BacklogItem.new(title="long run", objective="keep training")
    backlog.add(running)
    backlog.mark_running(running.id)

    note = supervisor._planner_current_reality_note()
    time_lines = [line for line in note.splitlines() if line.startswith("- time:")]

    assert len(time_lines) == 1
    line = time_lines[0]
    assert "30.0h remaining" in line
    assert "median 4.0h over 3" in line
    assert running.id in line


def test_digest_time_block_says_when_no_deadline_or_history(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")

    note = supervisor._planner_current_reality_note()

    line = next(row for row in note.splitlines() if row.startswith("- time:"))
    assert "no deadline" in line
    assert "no completed missions" in line


def test_crashed_missions_do_not_drag_the_measured_median_down(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    backlog = supervisor.memory.backlog
    _finished(backlog, "train", 5.0)
    now = time.time()
    for index in range(6):
        crash = BacklogItem.new(title=f"crash {index}", objective="start")
        backlog.add(crash)
        backlog.update(
            crash.id, status="failed", started_ts=now - 100, finished_ts=now - 95
        )

    note = supervisor._planner_current_reality_note()

    line = next(row for row in note.splitlines() if row.startswith("- time:"))
    assert "median 5.0h over 1" in line
    assert "6 failed/aborted not counted" in line


def test_elapsed_time_falls_back_to_project_creation(tmp_path: Path) -> None:
    import json

    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    (supervisor.memory.root / "session.json").write_text(
        json.dumps({"created": time.time() - 3 * 86400}), encoding="utf-8"
    )

    note = supervisor._planner_current_reality_note()

    line = next(row for row in note.splitlines() if row.startswith("- time:"))
    assert "project started 72.0h ago" in line


def test_split_memory_daemon_reads_project_creation_from_the_project(
    tmp_path: Path,
) -> None:
    """Web and CLI daemons hold split memory whose ``root`` is the shared home.

    The project's session record lives under the project's own state
    directory, so the elapsed-time anchor must be read from there.
    """
    import json

    from argus.life.memory import GlobalMemory, MemoryBundle, ProjectMemory

    global_root = tmp_path / "home"
    workspace = tmp_path / "work"
    workspace.mkdir()
    project = ProjectMemory.open("s-clock", label="s-clock", global_root=global_root)
    memory = MemoryBundle(
        global_mem=GlobalMemory.open(global_root),
        project=project,
        project_worktree=workspace,
    )
    memory.init()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=object(),
        sink=JsonlEventSink(None, life_dir=project.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="train and evaluate",
            open_ended=True,
            project_worktree=workspace,
            artifact_root=workspace,
            project_state_dir=project.root,
        ),
    )
    persist_vertical(workspace, "software", workflow_mode="direct")
    (project.root / "session.json").write_text(
        json.dumps({"created": time.time() - 2 * 86400}), encoding="utf-8"
    )
    assert not (global_root / "session.json").exists()

    note = supervisor._planner_current_reality_note()

    line = next(row for row in note.splitlines() if row.startswith("- time:"))
    assert "project started 48.0h ago" in line


def test_finished_background_jobs_are_measured_separately(tmp_path: Path) -> None:
    import json

    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life")
    registry = project / ".argus_external_work"
    registry.mkdir()
    now = time.time()
    for name, hours, outcome in (
        ("a", 10.0, "completed"),
        ("b", 14.0, "completed"),
        ("c", 0.01, "failed: out of memory"),
    ):
        (registry / f"{name}.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "work_id": name,
                    "state": "terminal",
                    "outcome": outcome,
                    "started_at": now - hours * 3600 - 60,
                    "heartbeat_at": now - 60,
                }
            ),
            encoding="utf-8",
        )

    note = supervisor._planner_current_reality_note()

    line = next(row for row in note.splitlines() if row.startswith("- time:"))
    assert "finished background jobs: median 12.0h over 2" in line
