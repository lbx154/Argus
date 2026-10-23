"""Tasks the model service keeps failing stop after the automatic-retry window.

A task the model service failed goes back to the Planner, which can dispatch
the next task into the same outage. Once such failures have run back to back
for the window, the next one waits for the operator's Run instead, and the
circuit its park opens holds every later task without a call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from argus.core.autonomy import AUTOMATIC_RETRY_WINDOW_SECONDS, RETRY_WINDOW_SPENT_REASON
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.mission_outcome import mission_outcome_dimensions
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from tests.manager_inbox import team_reports

_ROUTED = {"routed": True, "vertical": "software"}


class _Sink:
    def handle_event(self, event: dict[str, Any]) -> None:
        pass


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_kind: str | None = None
    recoverable: bool = False
    stop_reason: str = ""
    rounds: int = 1


class _ReviewerOutageRunner:
    """Every task ends because the Reviewer's model service gave no judgment."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **_kwargs: Any) -> _Outcome:
        self.calls += 1
        return _Outcome(
            success=False,
            status="error",
            stop_kind="backend_unavailable",
            stop_reason="The Reviewer could not reach a judgment 3 times in a row.",
        )


def _ended(memory: LifeMemory, *, ago: float, stop_kind: str | None) -> None:
    item = BacklogItem.new(title="earlier", objective="earlier work", manager_decision=_ROUTED)
    item.status = "failed"
    item.finished_ts = time.time() - ago
    item.outcome = mission_outcome_dimensions(status="failed", success=False, stop_kind=stop_kind)
    memory.backlog.add(item)


def _supervisor(tmp_path, memory: LifeMemory, runner) -> LifeSupervisor:
    return LifeSupervisor(
        memory=memory, runner=runner, sink=_Sink(),
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )


def test_tasks_failing_on_the_model_service_stop_after_three_hours(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    _ended(memory, ago=AUTOMATIC_RETRY_WINDOW_SECONDS + 60, stop_kind="backend_unavailable")
    _ended(memory, ago=1800, stop_kind="transient_error")
    task = memory.backlog.add(BacklogItem.new(title="next", objective="next work", manager_decision=_ROUTED))
    runner = _ReviewerOutageRunner()
    supervisor = _supervisor(tmp_path, memory, runner)

    supervisor.run()

    fenced = next(item for item in memory.backlog.all() if item.id == task.id)
    assert fenced.status == "parked"
    assert fenced.last_error.startswith(RETRY_WINDOW_SPENT_REASON)
    told = team_reports(tmp_path / "life")
    assert any("click Run" in turn["text"] for turn in told)
    # The model service would turn a later task away too: it waits, uncalled.
    later = memory.backlog.add(BacklogItem.new(title="later", objective="later work", manager_decision=_ROUTED))
    for _ in range(3):
        assert supervisor.run()["stopped_by"] == "runtime_block"
    assert runner.calls == 1
    assert next(item for item in memory.backlog.all() if item.id == later.id).status == "pending"


def test_a_younger_outage_still_fails_the_task_into_the_plan(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    _ended(memory, ago=600, stop_kind="backend_unavailable")
    task = memory.backlog.add(BacklogItem.new(title="next", objective="next work", manager_decision=_ROUTED))

    _supervisor(tmp_path, memory, _ReviewerOutageRunner()).run()

    assert next(item for item in memory.backlog.all() if item.id == task.id).status == "failed"


def test_a_task_that_failed_on_its_own_merits_ends_the_outage_run(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    _ended(memory, ago=AUTOMATIC_RETRY_WINDOW_SECONDS + 60, stop_kind="backend_unavailable")
    _ended(memory, ago=1800, stop_kind=None)
    task = memory.backlog.add(BacklogItem.new(title="next", objective="next work", manager_decision=_ROUTED))

    _supervisor(tmp_path, memory, _ReviewerOutageRunner()).run()

    assert next(item for item in memory.backlog.all() if item.id == task.id).status == "failed"
