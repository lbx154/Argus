"""Replay of s-efe9deac, 10-26 13:24:48: two parallel workers claim both learning arms.

Each task asked for one GPU. The claim picked a card per task, stored it as
``gpu_indices`` and handed it to nobody; both Engineers then parked waiting for
a "scheduler-assigned GPU UUID" for 15 hours. The first Engineer prompt also
named the write boundary twice, and neither copy admitted ``.argus/evidence/``,
where the handoff rule tells the Engineer to put what the next holder reads.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from argus.life import memory as memory_module
from argus.life.context_packet import create_mission_context, render_mission_brief
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "s_efe9deac" / "claim_learning_arms.json").read_text(encoding="utf-8")
)


def _claim_both(tmp_path: Path, monkeypatch) -> tuple[LifeMemory, list[BacklogItem]]:
    monkeypatch.setattr(memory_module, "_gpu_cards", lambda: tuple(FIXTURE["cards_busy"]))
    memory = LifeMemory.open(tmp_path / "projects" / "s-efe9deac")
    for row in FIXTURE["tasks"]:
        memory.backlog.add(BacklogItem.from_jsonable(row))
    claimed = [
        memory.backlog.claim_next(parallel_only=True, owner=f"parallel-{worker}")
        for worker in (1, 2)
    ]
    return memory, claimed


def test_claiming_the_arms_admits_by_count_and_assigns_no_card(tmp_path: Path, monkeypatch) -> None:
    memory, claimed = _claim_both(tmp_path, monkeypatch)

    assert [item.id for item in claimed] == [row["id"] for row in FIXTURE["tasks"]]
    stored = [json.loads(line) for line in memory.backlog.path.read_text(encoding="utf-8").splitlines()]
    assert {row["status"] for row in stored} == {"running"}
    assert all("gpu_indices" not in row for row in stored)
    assert memory.backlog.gpu_summary() == {"total": 4, "busy": 1, "reserved": 2, "free": 2}


def test_first_engineer_prompt_names_one_write_boundary_with_evidence(tmp_path: Path, monkeypatch) -> None:
    memory, (item, _) = _claim_both(tmp_path, monkeypatch)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=SimpleNamespace(manager=None),
        sink=JsonlEventSink(None, life_dir=memory.root, verbosity="full"),
        config=LifeSupervisorConfig(continuous=True, open_ended=True),
    )
    mission = create_mission_context(
        life_dir=memory.root, mission_id=item.id, stage="experiment", scope="bounded",
        objective=item.objective, acceptance_check=item.acceptance_check,
        plan_hypothesis=item.plan_hypothesis, goal_contribution=item.goal_contribution,
        expected_regressions=item.expected_regressions, decision_rule=item.decision_rule,
        execution_workdir=str(tmp_path / "workspace"), owns_paths=item.owns_paths,
        non_goals=item.non_goals, plan_id=item.plan_id, plan_version=item.plan_version,
        node_key=item.node_key, tags=item.tags,
    )

    prompt = supervisor._build_mission_prelude(item, defer_memory=True) + "\n" + render_mission_brief(mission)

    boundary = [line for line in prompt.splitlines() if item.owns_paths[0] in line]
    assert len(boundary) == 1
    assert "`.argus/evidence/`" in boundary[0]
    assert "Do not write outside" not in prompt
