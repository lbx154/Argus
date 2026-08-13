"""Regression tests for mission outcome normalization and emission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from argus_skill.apps._runtime import _ExecuteState, _SkillLoopRunner
from argus_skill.core.models import LoopOutcome, ReviewDecision, RoundRecord
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
    final_review_status: str = ""
    final_review_source: str = ""
    final_review_reason: str = ""
    final_message: str = ""
    summary: str = ""
    final_output: str = ""


class _FixedOutcomeRunner:
    def __init__(self, outcome: _Outcome) -> None:
        self._outcome = outcome
        self.kwargs: dict[str, Any] = {}

    def execute(self, **kwargs: Any) -> _Outcome:
        self.kwargs = kwargs
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


def test_completed_event_carries_existing_engineer_summary(tmp_path) -> None:
    supervisor, sink = _make_supervisor(
        tmp_path,
        _Outcome(
            success=True,
            status="done",
            summary="Created RESULT.txt and verified its exact contents.",
            final_review_reason="Reviewer accepted the file.",
        ),
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(title="Create result", objective="Create RESULT.txt")
    )

    supervisor.tick()

    assert _completed_event(sink)["summary"] == (
        "Created RESULT.txt and verified its exact contents."
    )


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


def test_first_independent_success_promotes_learned_vertical(tmp_path) -> None:
    from argus_skill.verticals._data_domain import (
        load_data_domain,
        write_data_domain,
    )

    supervisor, _sink = _make_supervisor(
        tmp_path,
        _Outcome(
            success=True,
            status="done",
            final_review_status="done",
            final_review_source="reviewer",
            final_review_reason="The learned workflow passed.",
        ),
    )
    write_data_domain(
        supervisor.memory.root,
        "device_tuning",
        stages=["inspect", "tune"],
        status="candidate",
        purpose="tune unfamiliar local devices",
        require_independent_review=True,
    )
    item = supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Tune the device",
            objective="Tune this local device",
            tags=["review:required"],
            manager_decision={
                "routed": True,
                "vertical": "device_tuning",
                "learned_vertical_status": "candidate",
            },
        )
    )

    supervisor.tick()

    assert load_data_domain("device_tuning", supervisor.memory.root).status == "formal"
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.manager_decision["learned_vertical_status"] == "formal"
    assert (
        supervisor.memory.root
        / "learned_verticals"
        / "device_tuning.json"
    ).is_file()


def test_promotion_write_failure_does_not_undo_successful_mission(
    tmp_path,
    monkeypatch,
) -> None:
    from argus_skill.verticals._data_domain import (
        load_data_domain,
        write_data_domain,
    )

    supervisor, sink = _make_supervisor(
        tmp_path,
        _Outcome(
            success=True,
            status="done",
            final_review_status="done",
            final_review_source="reviewer",
        ),
    )
    write_data_domain(
        supervisor.memory.root,
        "device_tuning",
        stages=["inspect", "tune"],
        status="candidate",
        purpose="tune unfamiliar local devices",
    )
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Tune the device",
            objective="Tune this local device",
            manager_decision={
                "routed": True,
                "vertical": "device_tuning",
                "learned_vertical_status": "candidate",
            },
        )
    )
    def fail_promotion(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "argus_skill.verticals._data_domain.promote_data_domain",
        fail_promotion,
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    assert load_data_domain("device_tuning", supervisor.memory.root).status == "candidate"
    assert any(
        event.get("type") == "life.learned_vertical.promotion_failed"
        for event in sink.events
    )


def test_reviewer_receives_compact_task_contract_not_engineer_prelude(
    tmp_path,
) -> None:
    supervisor, _sink = _make_supervisor(
        tmp_path,
        _Outcome(success=True, status="done"),
    )
    supervisor.config.runtime_context = "large engineer-only runtime context"
    supervisor.memory.backlog.add(
        BacklogItem.new(
            title="Compact review",
            objective="Implement the kernel change.",
            acceptance_check="benchmark exits zero",
            non_goals=["do not change the public API"],
        )
    )

    supervisor.tick()

    review_objective = supervisor.runner.kwargs["review_objective"]
    assert review_objective == (
        "Implement the kernel change.\n"
        "Acceptance check: benchmark exits zero\n"
        "Non-goals: do not change the public API"
    )
    assert "engineer-only runtime context" not in review_objective

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


def test_replan_reason_survives_runtime_and_supervisor_adapters(tmp_path) -> None:
    review_reason = "Reviewer certified that the active node is refuted."
    loop_outcome = LoopOutcome(
        status="replan_requested",
        rounds=[
            RoundRecord(
                round_index=1,
                engineer_message="",
                engineer_exit_code=0,
                review=ReviewDecision(
                    status="replan_requested",
                    reason=review_reason,
                    next_action="replace the active plan",
                ),
            )
        ],
        final_message="",
        reason="",
        workdir=str(tmp_path),
    )
    execute_state = _ExecuteState()
    execute_state.outcome = loop_outcome
    execute_state.effective_status = "replan_requested"
    runner = _SkillLoopRunner.__new__(_SkillLoopRunner)
    runtime_outcome = runner._build_execute_outcome(execute_state)
    supervisor, _sink = _make_supervisor(tmp_path, runtime_outcome)
    supervisor.memory.backlog.add(
        BacklogItem.new(title="replan mission", objective="replace the active plan")
    )

    result = supervisor.tick()

    assert runtime_outcome.final_review_reason == review_reason
    assert result is not None
    assert result["review_reason"] == review_reason


def test_research_result_survives_runtime_and_mission_event(tmp_path) -> None:
    research_result = {
        "result_class": "literature_review",
        "correctness_status": "verified",
        "novelty_status": "known",
        "significance_status": "publishable",
        "statement_fidelity_status": "verified",
        "evidence": ["source audit"],
        "limitations": [],
    }
    loop_outcome = LoopOutcome(
        status="done",
        rounds=[
            RoundRecord(
                round_index=1,
                engineer_message=(
                    "Wrote the survey and verified every cited source.\n\n"
                    "MILESTONE_STATUS=done\n"
                    "OPERATOR_QUESTION=none"
                ),
                engineer_exit_code=0,
                review=ReviewDecision(
                    status="done",
                    reason="The survey is complete.",
                    next_action="",
                    research_result=research_result,
                ),
            )
        ],
        final_message=(
            "Wrote the survey and verified every cited source.\n\n"
            "MILESTONE_STATUS=done\n"
            "OPERATOR_QUESTION=none"
        ),
        reason="",
        workdir=str(tmp_path),
    )
    execute_state = _ExecuteState()
    execute_state.outcome = loop_outcome
    execute_state.effective_status = "done"
    runtime_outcome = _SkillLoopRunner.__new__(
        _SkillLoopRunner
    )._build_execute_outcome(execute_state)
    supervisor, sink = _make_supervisor(tmp_path, runtime_outcome)
    supervisor.memory.backlog.add(
        BacklogItem.new(title="survey", objective="write the review")
    )

    supervisor.tick()

    assert runtime_outcome.research_result == research_result
    assert runtime_outcome.summary == (
        "Wrote the survey and verified every cited source."
    )
    assert _completed_event(sink)["research_result"] == research_result
    assert _completed_event(sink)["summary"] == (
        "Wrote the survey and verified every cited source."
    )


def test_long_engineer_handoff_survives_compact_mission_summary(tmp_path) -> None:
    body = "# Complete report\n\n" + "\n\n".join(
        f"Section {index}: verified result with supporting detail."
        for index in range(80)
    )
    wire_message = (
        f"{body}\n\n"
        "MILESTONE_STATUS=done\n"
        "OPERATOR_QUESTION=none\n"
        "OPERATOR_OPTIONS=none"
    )
    loop_outcome = LoopOutcome(
        status="done",
        rounds=[
            RoundRecord(
                round_index=1,
                engineer_message=wire_message,
                engineer_exit_code=0,
                review=ReviewDecision(
                    status="done",
                    reason="The report is complete.",
                    next_action="",
                ),
            )
        ],
        final_message=wire_message,
        reason="",
        workdir=str(tmp_path),
    )
    execute_state = _ExecuteState()
    execute_state.outcome = loop_outcome
    execute_state.effective_status = "done"
    runtime_outcome = _SkillLoopRunner.__new__(
        _SkillLoopRunner
    )._build_execute_outcome(execute_state)
    supervisor, sink = _make_supervisor(tmp_path, runtime_outcome)
    supervisor.memory.backlog.add(
        BacklogItem.new(title="long report", objective="deliver the full report")
    )

    supervisor.tick()

    assert len(runtime_outcome.summary) == 1200
    assert runtime_outcome.final_output == body
    assert _completed_event(sink)["summary"] == runtime_outcome.summary
    assert _completed_event(sink)["final_output"] == body


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
