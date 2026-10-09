"""A reached per-mission budget becomes an operator decision, never a silent stop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus.core.budget_signal import (
    MissionBudget,
    mission_usage_summary,
    record_mission_budget_pause,
)
from argus.core.event_catalog import EventType
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig


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


class _BudgetStopRunner:
    """Stands in for the round loop stopping at the operator's budget."""

    def __init__(self, memory: LifeMemory, *, marker: bool) -> None:
        self.memory = memory
        self.marker = marker

    def execute(self, **kwargs: Any) -> _Outcome:
        item_id = str(kwargs["usage_mission_id"]).split(":attempt:", 1)[0]
        if self.marker:
            record_mission_budget_pause(
                self.memory.root, item_id, reached="12 of 10 premium requests",
                summary=mission_usage_summary(self.memory.root, item_id),
                budget=MissionBudget(requests=10),
            )
        return _Outcome(success=False, status="paused_operator", stop_reason="budget")


def _supervisor(memory: LifeMemory, runner: Any, sink: _Sink) -> LifeSupervisor:
    return LifeSupervisor(
        memory=memory, runner=runner, sink=sink,
        config=LifeSupervisorConfig(budget=LifeBudget(max_missions=3), poll_interval_seconds=0.01),
    )


def test_budget_stop_parks_the_task_with_an_operator_decision(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    item = memory.backlog.add(BacklogItem.new(
        title="long task", objective="keep going",
        manager_decision={"routed": True, "vertical": "software"},
    ))

    result = _supervisor(memory, _BudgetStopRunner(memory, marker=True), sink).tick()

    assert result is not None and result["status"] == "paused_operator"
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_operator"
    assert "per-mission budget" in stored.pending_question
    card = stored.operator_decision
    assert card["decision_kind"] == "mission_budget"
    assert {option["id"] for option in card["options"]} == {"resume", "drop"}
    asked = [event for event in sink.events if event.get("type") == EventType.LIFE_OPERATOR_QUESTION_PENDING]
    assert asked and asked[-1]["item_id"] == item.id


def test_chinese_task_gets_a_chinese_question(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(
        title="整理实验结果", objective="继续推进",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    _supervisor(memory, _BudgetStopRunner(memory, marker=True), _Sink()).tick()
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert "单任务预算" in stored.pending_question


def test_manager_wait_without_budget_marker_keeps_its_own_path(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(BacklogItem.new(
        title="task", objective="work",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    _supervisor(memory, _BudgetStopRunner(memory, marker=False), _Sink()).tick()
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.operator_decision.get("decision_kind") != "mission_budget"
