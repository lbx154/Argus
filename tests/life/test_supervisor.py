from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from argus_skill.core.pricing import usd_for_tokens
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool = True
    status: str = "done"
    stop_reason: str = ""
    rounds: int = 1
    matched_skill_name: str = ""
    skill_distilled: bool = True
    had_follow_up: bool = False
    final_message: str = "done"


class _ScientistSpendRunner:
    def execute(
        self,
        *,
        objective: str,
        sink: Any,
        prelude_context: str = "",
        scope: str = "",
        original_objective: str = "",
    ) -> _Outcome:
        sink.handle_event({
            "type": "skill.cost.completed",
            "agent_layer": "scientist",
            "matcher_model": "gpt-5.5",
            "distiller_model": "gpt-5.5-mini",
            "matcher": {
                "model": "gpt-5.5",
                "input_tokens": 200_000,
                "cached_input_tokens": 0,
                "output_tokens": 1_000,
            },
            "distiller": {
                "model": "gpt-5.5-mini",
                "input_tokens": 100_000,
                "cached_input_tokens": 50_000,
                "output_tokens": 2_000,
            },
            "usage_scope": "delta",
        })
        return _Outcome()


def test_skill_miss_scientist_spend_is_journaled_and_budgeted(
    tmp_path,
) -> None:
    mem = LifeMemory.open(tmp_path / "life")
    runner = _ScientistSpendRunner()
    sink = _RecordingSink()
    cfg = LifeSupervisorConfig(
        budget=LifeBudget(
            per_mission_cap_usd=1.0,
            daily_cap_usd=0.20,
            max_missions=2,
        ),
        poll_interval_seconds=0.01,
    )
    sup = LifeSupervisor(memory=mem, runner=runner, sink=sink, config=cfg)

    first = mem.backlog.add(BacklogItem.new(
        title="skill miss",
        objective="force a skill miss and distill",
        max_cost_usd=0.01,
    ))
    second = mem.backlog.add(BacklogItem.new(
        title="next mission",
        objective="should be held by daily budget",
        max_cost_usd=0.01,
    ))

    result = sup.tick()

    expected_scientist_usd = usd_for_tokens(
        "gpt-5.5",
        200_000,
        0,
        1_000,
    ) + usd_for_tokens("gpt-5.5-mini", 100_000, 50_000, 2_000)
    assert result is not None
    assert result["success"] is True
    completed = [entry for entry in mem.journal.all() if entry.kind == "mission_complete"]
    assert len(completed) == 1
    entry = completed[0]
    assert entry.cost_usd == pytest.approx(expected_scientist_usd)
    assert entry.extra["scientist_cost_usd"] == pytest.approx(expected_scientist_usd)
    assert entry.extra["scientist_input_tokens"] == 300_000
    assert entry.extra["input_tokens"] == 300_000
    assert mem.backlog.all()[0].id == first.id
    assert mem.backlog.all()[0].status == "done"

    blocked = sup.tick()

    assert blocked is not None
    assert blocked["status"] == "budget_pause"
    assert blocked["item_id"] == second.id
    assert "daily budget remaining" in blocked["reason"]

