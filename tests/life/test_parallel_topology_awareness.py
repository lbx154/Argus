"""The Planner can only fill a spare mission slot if the host tells it the
slot exists and what unlocks it.

Width-2 daemons ran serially in practice: the auxiliary supervisor admits
only tasks whose whole co-running set declares ``parallel_safe`` with
disjoint ``owns_paths``, but the Planner was never shown the slot count,
the running tasks' ownership metadata, or the fact that its own
``TASK_PARALLEL_SAFE`` flag was silently stripped when ``TASK_OWNS_PATHS``
was missing. These tests pin the three feedback surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.life.event_log import JsonlEventSink
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from argus_skill.planner import PlannerConfig
from argus_skill.roles.prompts.planner import build_continuous_prompt
from argus_skill.skills.vertical_select import persist_vertical


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return RunnerResult(exit_code=0, agent_messages=[self.replies.pop(0)])


def _supervisor(
    project: Path,
    life: Path,
    planner: _PlannerBackend,
    *,
    mission_slots: int = 1,
) -> LifeSupervisor:
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_MissionRunner(),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(),
            continuous=True,
            continuous_objective="keep optimizing Argus",
            open_ended=True,
            project_worktree=project,
            artifact_root=project,
            mission_slots=mission_slots,
        ),
        planner_runner=planner,
    )
    persist_vertical(project, "software", workflow_mode="direct")
    supervisor._vertical_resolved = True
    supervisor._planner_config = lambda: PlannerConfig(  # type: ignore[method-assign]
        working_dir=str(project),
        open_ended=True,
    )
    return supervisor


def _running_item(title: str, **fields) -> BacklogItem:
    item = BacklogItem.new(title=title, objective=f"objective for {title}")
    return item


def test_digest_reports_slot_topology_and_ownership_metadata(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(
        project, tmp_path / "life", _PlannerBackend([]), mission_slots=2
    )
    backlog = supervisor.memory.backlog
    running = BacklogItem.new(
        title="long serial mission",
        objective="drive the current experiment",
    )
    backlog.add(running)
    backlog.mark_running(running.id)
    pending = BacklogItem.new(
        title="independent side inquiry",
        objective="answer an unrelated question",
    )
    backlog.add(pending)

    note = supervisor._planner_current_reality_note()

    assert "mission_slots" in note
    assert "2" in note.split("mission_slots", 1)[1].splitlines()[0]
    # Each active task line must show what the admission gate will read.
    running_line = next(
        line for line in note.splitlines() if "long serial mission" in line
    )
    assert "parallel_safe=false" in running_line
    # A spare slot exists but the running task declares no ownership: the
    # digest must say so, or the Planner cannot know why width stays 1.
    assert "TASK_OWNS_PATHS" in note


def test_digest_stays_quiet_for_serial_campaigns(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(
        project, tmp_path / "life", _PlannerBackend([]), mission_slots=1
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(title="only mission", objective="do the work")
    )

    note = supervisor._planner_current_reality_note()

    assert "mission_slots" not in note
    assert "parallel_safe=" not in note


def test_digest_shows_declared_ownership_without_the_advisory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(
        project, tmp_path / "life", _PlannerBackend([]), mission_slots=2
    )
    backlog = supervisor.memory.backlog
    running = BacklogItem.new(
        title="owned analysis lane",
        objective="analyze the first dataset",
        parallel_safe=True,
        owns_paths=["analysis/lane-a"],
    )
    backlog.add(running)
    backlog.mark_running(running.id)

    note = supervisor._planner_current_reality_note()

    owned_line = next(
        line for line in note.splitlines() if "owned analysis lane" in line
    )
    assert "parallel_safe=true" in owned_line
    assert "analysis/lane-a" in owned_line


def test_digest_flags_paused_external_work_without_ownership(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(
        project, tmp_path / "life", _PlannerBackend([]), mission_slots=2
    )
    backlog = supervisor.memory.backlog
    paused = BacklogItem.new(
        title="waiting on the training run",
        objective="resume once the external job lands",
    )
    backlog.add(paused)
    backlog.update(paused.id, status="paused_external_work")

    note = supervisor._planner_current_reality_note()

    # The claim gate refuses every candidate while a paused external job has
    # no owned paths; advertising the slot as usable would mislead the plan.
    assert "runs or waits without declared path ownership" in note
    assert "can run now" not in note


def test_enqueue_reports_dropped_parallel_flags_once(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    planner = _PlannerBackend([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=split the survey into two independent lanes",
            "TASK_KEY=lane-a",
            "TASK_TITLE=Survey lane A",
            "TASK_OBJECTIVE=Read the first half of the corpus.",
            "TASK_ACCEPTANCE_CHECK=notes for lane A exist",
            "TASK_PARALLEL_SAFE=true",
            "TASK_KEY=lane-b",
            "TASK_TITLE=Survey lane B",
            "TASK_OBJECTIVE=Read the second half of the corpus.",
            "TASK_ACCEPTANCE_CHECK=notes for lane B exist",
            "TASK_PARALLEL_SAFE=true",
            "TASK_OWNS_PATHS=survey/lane-b",
        ])
    ])
    supervisor = _supervisor(
        project, tmp_path / "life", planner, mission_slots=2
    )

    assert supervisor._plan_next_work() is True

    pending = {item.title: item for item in supervisor.memory.backlog.pending()}
    assert pending["Survey lane B"].parallel_safe is True
    assert pending["Survey lane A"].parallel_safe is False

    note = supervisor._planner_dropped_parallel_runtime_note()
    assert "Survey lane A" in note
    assert "TASK_OWNS_PATHS" in note
    assert "Survey lane B" not in note
    # One-shot: consumed by the render above.
    assert supervisor._planner_dropped_parallel_runtime_note() == ""

    events = [
        json.loads(line)
        for line in (supervisor.memory.root / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    dropped = [
        event
        for event in events
        if event.get("type") == "life.planner.parallel_dropped"
    ]
    assert len(dropped) == 1
    assert dropped[0].get("title") == "Survey lane A"


def test_glob_owned_paths_never_advertise_a_slot(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    planner = _PlannerBackend([
        "\n".join([
            "PROJECT_DONE=false",
            "REASON=parallelize the sweep",
            "TASK_KEY=sweep",
            "TASK_TITLE=Sweep the source tree",
            "TASK_OBJECTIVE=Scan every module for the pattern.",
            "TASK_ACCEPTANCE_CHECK=scan notes exist",
            "TASK_PARALLEL_SAFE=true",
            "TASK_OWNS_PATHS=src/**",
        ])
    ])
    supervisor = _supervisor(
        project, tmp_path / "life", planner, mission_slots=2
    )

    assert supervisor._plan_next_work() is True

    (item,) = supervisor.memory.backlog.pending()
    # The claim gate rejects glob syntax outright; a persisted glob would
    # advertise a task as slot-ready that can never be admitted.
    assert item.parallel_safe is False
    assert item.owns_paths == []
    note = supervisor._planner_dropped_parallel_runtime_note()
    assert "Sweep the source tree" in note
    assert "wildcards" in note


def test_continuous_prompt_states_what_unlocks_a_slot() -> None:
    text = build_continuous_prompt(
        continuous_objective="keep optimizing Argus",
        journal_tail="",
        planning_cycle=1,
    )
    assert "co-run" in text
    assert "no wildcards" in text
