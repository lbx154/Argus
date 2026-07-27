"""Regression tests for mission outcome normalization and emission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.mission_outcome import (
    mission_outcome_class,
    mission_outcome_dimensions,
)
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
    stop_reason: str = ""
    stop_kind: str | None = None
    recoverable: bool = False
    rounds: int = 1


class _FixedOutcomeRunner:
    def __init__(self, outcome: _Outcome) -> None:
        self._outcome = outcome

    def execute(self, **_kwargs: Any) -> _Outcome:
        return self._outcome


def _completed_event(sink: _Sink) -> dict[str, Any]:
    return next(event for event in sink.events if event.get("type") == "life.mission.completed")


def _make_supervisor(tmp_path, outcome: _Outcome) -> tuple[LifeSupervisor, _Sink]:
    memory = LifeMemory.open(tmp_path / "life")
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_FixedOutcomeRunner(outcome),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            poll_interval_seconds=0.01,
        ),
    )
    return supervisor, sink


@pytest.mark.parametrize(
    ("status", "success", "expected"),
    [
        ("done", True, "completed"),
        ("completed", False, "completed"),
        ("research_incomplete", False, "incomplete"),
        ("paused_no_breakthrough", False, "incomplete"),
        ("exhausted_current_methods", False, "incomplete"),
        ("infra_blocked", False, "blocked"),
        ("no_progress", False, "stalled"),
        ("max_rounds", False, "stalled"),
        ("blocked", False, "blocked"),
        ("replan_requested", False, "ended"),
        ("error", False, "failed"),
        ("supervisor_error", False, "failed"),
        ("paused_budget", False, "ended"),
        ("paused_daemon_shutdown", False, "ended"),
        ("paused_operator", False, "ended"),
        ("aborted", False, "ended"),
        ("legacy_unknown_status", False, "ended"),
    ],
)
def test_mission_outcome_classifies_statuses(
    status: str,
    success: bool,
    expected: str,
) -> None:
    assert mission_outcome_class(status=status, success=success) == expected


def test_review_only_outcome_marks_stage_transition_intentionally_skipped() -> None:
    outcome = mission_outcome_dimensions(
        status="done",
        success=True,
        review_status="done",
        stage_transition_skipped=True,
    )

    assert outcome["stage_certification"] == "intentionally_skipped"


@pytest.mark.parametrize(
    ("status", "success", "expected"),
    [
        ("done", True, "completed"),
        ("blocked", False, "blocked"),
    ],
)
def test_normal_completion_events_include_outcome_class(
    tmp_path,
    status: str,
    success: bool,
    expected: str,
) -> None:
    supervisor, sink = _make_supervisor(
        tmp_path,
        _Outcome(success=success, status=status, stop_reason="mission finished"),
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(title=f"{status} mission", objective="exercise event payload")
    )

    result = supervisor.tick()

    assert result is not None
    assert _completed_event(sink)["outcome_class"] == expected

def test_pause_completion_event_includes_outcome_class(tmp_path) -> None:
    supervisor, sink = _make_supervisor(
        tmp_path,
        _Outcome(
            success=False,
            status="paused_budget",
            stop_kind="budget_exhausted",
            recoverable=True,
            stop_reason="per-attempt cap reached",
        ),
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(title="paused mission", objective="exercise pause payload")
    )

    result = supervisor.tick()

    assert result is not None
    assert _completed_event(sink)["outcome_class"] == "ended"


def test_daemon_shutdown_is_persisted_as_recoverable_pause(tmp_path) -> None:
    supervisor, sink = _make_supervisor(
        tmp_path,
        _Outcome(
            success=False,
            status="paused_daemon_shutdown",
            stop_kind="daemon_shutdown",
            recoverable=True,
            stop_reason="daemon shutdown requested",
        ),
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="paused mission", objective="resume after restart")
    )

    result = supervisor.tick()

    assert result is not None and result["status"] == "paused_daemon_shutdown"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "paused_daemon_shutdown"
    completed = _completed_event(sink)
    assert completed["success"] is False
    assert completed["stop_kind"] == "daemon_shutdown"
    assert completed["recoverable"] is True


def test_operator_abort_is_terminal_but_not_failed(tmp_path) -> None:
    supervisor, sink = _make_supervisor(
        tmp_path,
        _Outcome(
            success=False,
            status="aborted",
            stop_kind="operator_abort",
            stop_reason="operator aborted this mission",
        ),
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="aborted mission", objective="stop only this item")
    )

    result = supervisor.tick()

    assert result is not None and result["status"] == "aborted"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "aborted"
    completed = _completed_event(sink)
    assert completed["success"] is False
    assert completed["stop_kind"] == "operator_abort"
    assert completed["failure_reason"] == ""


def test_completed_review_and_stage_are_independent() -> None:
    outcome = mission_outcome_dimensions(
        status="done",
        success=True,
        review_status="done",
        stage_transition={"action": "hold"},
    )

    assert outcome == {
        "execution_status": "completed",
        "review_status": "done",
        "stage_certification": "not_certified",
        "interruption_kind": "none",
        "resumable": False,
    }

def test_supervisor_error_recovery_event_includes_outcome_class(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    item = memory.backlog.add(
        BacklogItem.new(title="running mission", objective="recover after supervisor error")
    )
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=object(),
        sink=sink,
        config=LifeSupervisorConfig(
            budget=LifeBudget(max_missions=1),
            poll_interval_seconds=0.01,
        ),
    )
    memory.backlog.mark_running(item.id)

    recovered = supervisor._fail_running_items_after_supervisor_error("boom")

    assert recovered == [item.id]
    assert _completed_event(sink)["outcome_class"] == "failed"
