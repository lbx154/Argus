"""The Planner can only fill a spare mission slot if the host tells it the
slot exists and what unlocks it.

Width-2 daemons ran serially in practice: the auxiliary supervisor admits
only tasks whose whole co-running set declares ``parallel_safe`` with
disjoint ``owns_paths``, but the Planner was never shown the slot count
or the running tasks' ownership metadata. These tests pin those feedback
surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.models import RunnerResult
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import (
    LifeBudget,
    LifeSupervisor,
    LifeSupervisorConfig,
)
from argus.planner import PlannerConfig
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import plan, submit


class _MissionRunner:
    pass


class _PlannerBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def run_exec(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        submit(kwargs["options"], self.replies.pop(0))
        return RunnerResult(exit_code=0)


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
    assert "owns_paths" in note


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



def _two_arm_plan(path_a: list[str], path_b: list[str]) -> tuple[str, dict]:
    return plan(summary="run both arms on the shared implementation", tasks=[
        {
            "key": "arm-cpc", "title": "Run the CPC arm", "objective": "Train and evaluate the CPC arm.",
            "acceptance_check": "analysis.json for the CPC arm exists", "parallel_safe": True, "owns_paths": path_a,
        },
        {
            "key": "arm-general", "title": "Run the general arm", "objective": "Train and evaluate the general arm.",
            "acceptance_check": "analysis.json for the general arm exists", "parallel_safe": True, "owns_paths": path_b,
        },
    ])


def test_owned_paths_co_run_through_enqueue_and_claim(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    planner = _PlannerBackend([_two_arm_plan([".argus/runs/transfer/cpc"], [".argus/runs/transfer/general"])])
    supervisor = _supervisor(project, tmp_path / "life", planner, mission_slots=2)

    assert supervisor._plan_next_work() is True
    assert supervisor._planner_dropped_parallel_runtime_note() == ""

    backlog = supervisor.memory.backlog
    first = backlog.claim_next(respect_running=True)
    second = backlog.claim_next(parallel_only=True, respect_running=True)
    assert first is not None and second is not None
    assert {first.title, second.title} == {"Run the CPC arm", "Run the general arm"}
    assert {tuple(first.owns_paths), tuple(second.owns_paths)} == {
        (".argus/runs/transfer/cpc",), (".argus/runs/transfer/general",),
    }


def test_nested_owned_paths_stay_serial_after_claim(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    planner = _PlannerBackend([_two_arm_plan([".argus/runs/transfer"], [".argus/runs/transfer/general"])])
    supervisor = _supervisor(project, tmp_path / "life", planner, mission_slots=2)

    assert supervisor._plan_next_work() is True

    backlog = supervisor.memory.backlog
    assert backlog.claim_next(respect_running=True) is not None
    assert backlog.claim_next(parallel_only=True, respect_running=True) is None


def test_digest_renders_ownership_in_the_footer_syntax(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(
        project, tmp_path / "life", _PlannerBackend([]), mission_slots=2
    )
    running = BacklogItem.new(
        title="two-path lane",
        objective="write both outputs",
        parallel_safe=True,
        owns_paths=["runs/a", "runs/b"],
    )
    supervisor.memory.backlog.add(running)
    supervisor.memory.backlog.mark_running(running.id)

    note = supervisor._planner_current_reality_note()

    line = next(line for line in note.splitlines() if "two-path lane" in line)
    assert "owns_paths=runs/a|runs/b;" in line


def test_stage_package_flag_reaches_the_backlog_tags(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    planner = _PlannerBackend([plan(summary='package the first real comparison for the operator', tasks=[{'title': 'Package round one', 'objective': 'Assemble the reviewed results, failures and unmet requirements.', 'acceptance_check': 'every number cites its source file', 'stage_package': True}])])
    supervisor = _supervisor(project, tmp_path / "life", planner)

    assert supervisor._plan_next_work() is True

    (item,) = supervisor.memory.backlog.pending()
    assert "stage_package" in item.tags
    assert "review:required" in item.tags


@pytest.mark.parametrize(("mode", "phrase"), [
    ("broad", "working hypothesis you may revise"),
    ("locked", "only the operator changes its claim"),
])
def test_digest_states_who_owns_the_research_idea(tmp_path: Path, mode, phrase) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(project, tmp_path / "life", _PlannerBackend([]))
    state = project / ".argus" / "PIPELINE_STATE.json"
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["research_direction_mode"] = mode
    state.write_text(json.dumps(payload), encoding="utf-8")

    note = supervisor._planner_current_reality_note()

    line = next(line for line in note.splitlines() if "research_direction_mode" in line)
    assert mode in line and phrase in line


def test_digest_names_a_task_that_asked_for_more_gpus_than_the_machine_has(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.life import memory as memory_module

    monkeypatch.setattr(
        memory_module, "_gpu_cards", lambda: ((0, False), (1, False)),
    )
    project = tmp_path / "project"
    project.mkdir()
    supervisor = _supervisor(
        project, tmp_path / "life", _PlannerBackend([]), mission_slots=1
    )
    backlog = supervisor.memory.backlog
    huge = backlog.add(BacklogItem.new(
        title="train every arm at once", objective="train", gpu_count=8,
    ))
    assert backlog.next_pending() is None

    note = supervisor._planner_current_reality_note()

    line = next(line for line in note.splitlines() if "gpu_unfittable" in line)
    assert huge.id in line
    assert "asks for 8 GPUs but this machine has 2" in line
