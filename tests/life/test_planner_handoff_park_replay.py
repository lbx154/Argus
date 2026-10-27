"""Replay of s-efe9deac, 10-27 11:55:46: a planned task handed to the Planner.

The Reviewer handed 45caeeb6130c (plan-14a8957e331c v1) to the Planner while
its sibling 02a2583442f7 still ran in the other slot. Settlement put the row
back to pending; at 11:56:20, a second after the Planner started, the second
slot dispatched it to an Engineer again, who handed it back unchanged, and the
ladder parked it with the Manager. The Planner retired it at 12:00:09.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory, park_reason
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "planner_handoff_1155.json").read_text(encoding="utf-8")
)
SIBLING, SOURCE = FIXTURE["tasks"]
SETTLED, RETIRED = FIXTURE["settled_11_55_46"], FIXTURE["retired_12_00_09"]


class _Engineer:
    def execute(self, **_kwargs):  # noqa: ANN003
        return SimpleNamespace(**SETTLED, stage_transition={})


def _slot(root: Path, project: Path, runner: object, worker_id: str) -> LifeSupervisor:
    return LifeSupervisor(
        memory=LifeMemory.open(root),
        runner=runner,
        sink=JsonlEventSink(None, life_dir=root),
        config=LifeSupervisorConfig(
            mission_slots=2,
            parallel_worker=worker_id != "primary",
            coordinate_parallel_claims=True,
            worker_id=worker_id,
            project_worktree=project,
            artifact_root=project,
        ),
    )


def _started(root: Path, item_id: str) -> list[dict]:
    events = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return [e for e in events if e.get("type") == "life.mission.started" and e.get("item_id") == item_id]


def test_a_planned_task_handed_to_the_planner_waits_in_its_hands(tmp_path: Path) -> None:
    root, project = tmp_path / "life", tmp_path / "project"
    project.mkdir()
    primary = _slot(root, project, _Engineer(), "primary")
    second = _slot(root, project, object(), "parallel-1")
    memory = primary.memory
    sibling, source = (
        memory.backlog.add(BacklogItem.new(**{k: v for k, v in task.items() if k != "id"}, item_id=task["id"]))
        for task in (SIBLING, SOURCE)
    )
    assert memory.backlog.claim_next(expected_id=sibling.id, owner="parallel-1") is not None

    outcome = primary._run_one(source)

    assert outcome["status"] == "planner"
    row = {item.id: item for item in memory.backlog.all()}[SOURCE["id"]]
    assert (row.status, park_reason(row)) == ("parked", "research_open")

    # 11:56:20: while the Planner revises and the sibling still runs, the
    # other slot looks for work; the Planner's task is not on offer.
    assert second.tick() is None
    assert len(_started(root, SOURCE["id"])) == 1

    # 12:00:09: the Planner's retire is what ends it.
    assert memory.backlog.supersede_items(
        item_ids=(RETIRED["item_id"],),
        reason=RETIRED["reason"],
        superseded_by_plan_id=RETIRED["superseded_by_plan_id"],
        include_parked=True,
    ) == (SOURCE["id"],)
