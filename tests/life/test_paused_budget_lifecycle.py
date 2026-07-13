from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from argus_skill.life.memory import BacklogItem, IllegalStateTransition, LifeMemory
from argus_skill.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_kind: str | None = None
    recoverable: bool = False
    stop_reason: str = ""
    rounds: int = 1


class _PauseThenCompleteRunner:
    def __init__(self) -> None:
        self.usage_mission_ids: list[str] = []

    def execute(self, **kwargs: Any) -> _Outcome:
        self.usage_mission_ids.append(str(kwargs["usage_mission_id"]))
        if len(self.usage_mission_ids) == 1:
            return _Outcome(
                success=False,
                status="paused_budget",
                stop_kind="budget_exhausted",
                recoverable=True,
                stop_reason="per-attempt cap reached",
            )
        return _Outcome(success=True, status="done")


def test_paused_budget_is_recoverable_with_fresh_usage_attempt(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    runner = _PauseThenCompleteRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=3),
            poll_interval_seconds=0.01,
        ),
    )
    item = memory.backlog.add(
        BacklogItem.new(title="research", objective="continue from checkpoint")
    )

    paused = supervisor.tick()

    assert paused is not None
    assert paused["status"] == "paused_budget"
    assert paused["success"] is False
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_budget"
    assert stored.attempt == 1
    assert not [row for row in memory.backlog.all() if row.status == "failed"]
    with pytest.raises(IllegalStateTransition, match="resume_paused"):
        memory.backlog.update(item.id, status="pending")

    resumed = memory.backlog.resume_paused(item.id)
    assert resumed is not None
    assert resumed.status == "pending"
    assert resumed.attempt == 2

    completed = supervisor.tick()

    assert completed is not None and completed["success"] is True
    assert runner.usage_mission_ids == [
        f"{item.id}:attempt:1",
        f"{item.id}:attempt:2",
    ]


def test_budget_pause_backoff_never_starts_idle_timeout(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_PauseThenCompleteRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            continuous=True,
            continuous_objective="standing work",
        ),
    )

    supervisor._enter_pause_backoff()

    assert supervisor._idle_since is None
    assert supervisor._maybe_idle_timeout() == ""
