"""Replay of s-d9c7aeb2, 09-08 14:53 and 14:55: a review task parked before it began.

Both tasks asked the Reviewer to write paper/REVIEW.md. Keyword matching picked
them out, a tool-less Planner call answered in KEY=VALUE lines the host could
not settle, and the host parked each task on a question it wrote itself
("Should the current review receipt be checked outside the reviewed artifact...").
No Engineer ever ran. The fixture keeps both tasks as the Planner added them,
with the assessment the old preflight stored under manager_decision.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.apps._runtime_backends import _Outcome
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.skills.vertical_select import persist_vertical

ROWS = [
    json.loads(line)
    for line in (
        Path(__file__).resolve().parents[1] / "fixtures" / "s_d9c7aeb2" / "acceptance_preflight.jsonl"
    ).read_text(encoding="utf-8").splitlines()
]


class _Backend:
    def run_exec(self, **_):
        raise AssertionError("no Planner call stands between a task and its Engineer")


class _Runner:
    planner_backend = _Backend()

    def __init__(self):
        self.executed = []

    def execute(self, **kwargs):
        self.executed.append(kwargs)
        return _Outcome(
            success=True, status="done", final_review_status="done",
            final_review_source="reviewer", final_review_reason="Review written.",
        )


@pytest.mark.parametrize("row", ROWS, ids=[row["id"] for row in ROWS])
def test_review_task_goes_to_the_engineer_without_a_host_question(tmp_path: Path, row):
    workdir = tmp_path / "project"
    workdir.mkdir()
    persist_vertical(workdir, "research")
    memory = LifeMemory.open(tmp_path / "life")
    runner = _Runner()
    supervisor = LifeSupervisor(
        memory=memory, runner=runner,
        sink=SimpleNamespace(handle_event=lambda _event: None),
        config=LifeSupervisorConfig(project_worktree=workdir, artifact_root=workdir),
    )
    item = memory.backlog.add(BacklogItem.from_jsonable(row))

    supervisor.tick()

    assert len(runner.executed) == 1
    assert runner.executed[0]["objective"] == item.objective
    stored = next(entry for entry in memory.backlog.all() if entry.id == item.id)
    assert stored.status != "paused_operator"
    assert not stored.operator_decision
