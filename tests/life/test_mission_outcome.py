"""Regression tests for mission outcome normalization and emission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from argus.apps._runtime import _SkillLoopRunner
from argus.apps._runtime_helpers import _ExecuteState
from argus.core.models import LoopOutcome, ReviewDecision, RoundRecord
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.mission_outcome import (
    mission_outcome_dimensions,
    outcome_dimension_summary,
    review_keeps_mission_resumable,
)
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
    stop_reason: str = ""
    stop_kind: str | None = None
    recoverable: bool = False
    park_reason: str = ""
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


def test_outcome_summary_uses_human_labels_instead_of_raw_dimensions() -> None:
    summary = outcome_dimension_summary({
        "execution_status": "paused",
        "review_status": "continue",
        "stage_certification": "not_certified",
        "interruption_kind": "budget_exhausted",
        "resumable": True,
    })

    assert summary == [
        "Work paused",
        "Review requested another pass",
        "Stage remains open",
        "Stopped at the budget limit",
        "Can resume",
    ]
    assert not any("=" in part or "_" in part for part in summary)


def test_first_independent_success_promotes_learned_vertical(tmp_path) -> None:
    from argus.verticals._data_domain import (
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
    from argus.verticals._data_domain import (
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
        "argus.verticals._data_domain.promote_data_domain",
        fail_promotion,
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    assert load_data_domain("device_tuning", supervisor.memory.root).status == "candidate"
    assert any(
        event.get("type") == "life.learned_vertical.promotion_failed"
        for event in sink.events
    )


def test_long_engineer_handoff_survives_compact_mission_summary(tmp_path) -> None:
    from argus.core.mission_view import load_mission_view, update_mission_view_event

    body = "# Complete report\n\n" + "\n\n".join(
        f"Section {index}: verified result with supporting detail."
        for index in range(80)
    )
    wire_message = body
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
    assert _completed_event(sink)["summary"] == "The report is complete."
    assert _completed_event(sink)["final_output"] == body
    for event in sink.events:
        update_mission_view_event(tmp_path / "projected", event)
    mission = load_mission_view(tmp_path / "projected")["mission"]
    assert mission["summary"] == "The report is complete."
    assert mission["final_output"] == body


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


def test_review_continue_keeps_a_stalled_mission_resumable() -> None:
    """Run 17's settled mission, verbatim from its event log.

    The Reviewer answered ``continue`` and the round accounting said
    ``no_progress``; the status won, the mission was recorded terminal and
    non-resumable, and the project idled for five hours against an unfinished
    goal with nothing queued.
    """
    outcome = mission_outcome_dimensions(
        status="no_progress",
        success=False,
        review_status="continue",
        stage_transition_deferred=True,
    )

    assert outcome["execution_status"] == "paused"
    assert outcome["resumable"] is True
    assert outcome["review_status"] == "continue"
    # The stage verdict is a separate question and must not move with it.
    assert outcome["stage_certification"] == "deferred"


@pytest.mark.parametrize(
    ("status", "success", "review_status", "stop_kind"),
    [
        # An operator stop outranks any verdict.
        ("no_progress", False, "continue", "operator_abort"),
        ("aborted", False, "continue", None),
        # Blocked means a pending operator question; failed means a crash.
        ("blocked", False, "continue", None),
        ("error", False, "continue", None),
        # A stall the Reviewer did not answer with "continue" stays a stall.
        ("no_progress", False, "done", None),
        ("no_progress", False, "", None),
        # Success needs no resumption.
        ("done", True, "continue", None),
    ],
)
def test_review_continue_does_not_resume_other_terminal_states(
    status: str, success: bool, review_status: str, stop_kind: object
) -> None:
    assert not review_keeps_mission_resumable(
        status=status,
        success=success,
        review_status=review_status,
        stop_kind=stop_kind,
    )
    assert mission_outcome_dimensions(
        status=status,
        success=success,
        review_status=review_status,
        stop_kind=stop_kind,
    )["resumable"] is False


def test_an_old_background_job_pause_resumes_without_being_watched(tmp_path) -> None:
    """An older release parked a mission on a job it watched for the Engineer.
    The Engineer now waits on its own jobs, so the row simply runs again."""
    supervisor, _sink = _make_supervisor(tmp_path, _Outcome(success=True, status="done"))
    item = supervisor.memory.backlog.add(
        BacklogItem.new(title="benchmark", objective="evaluate the benchmark")
    )
    supervisor.memory.backlog.update(
        item.id,
        park_reason="external_work",
        outcome={"external_wait": {"kind": "subagent", "work_id": "job-1"}},
    )

    assert [row.id for row in supervisor._resume_automatic_pauses()] == [item.id]
    stored = next(row for row in supervisor.memory.backlog.all() if row.id == item.id)
    assert stored.status == "pending"


@pytest.mark.parametrize(
    ("tags", "delivered"),
    [
        (["manager", "scope:bounded", "review:required", "operator", "operator_priority"], True),
        (["planner", "scope:bounded", "review:required"], False),
    ],
)
def test_a_reviewed_operator_request_is_delivered_while_the_campaign_goes_on(
    tmp_path, tags: list[str], delivered: bool,
) -> None:
    supervisor, sink = _make_supervisor(tmp_path, _Outcome(
        success=True,
        status="done",
        final_review_status="done",
        final_review_source="reviewer",
        final_review_reason="中文综述 survey-zh.md 已交付并通过独立复核。",
    ))
    supervisor.config.continuous = True
    workdir = supervisor._project_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "survey-zh.md").write_text("# 综述\n", encoding="utf-8")
    supervisor.memory.backlog.add(
        BacklogItem.new(title="调研综述", objective="交付中文综述 survey-zh.md", tags=tags)
    )

    supervisor.tick()

    event = _completed_event(sink)
    assert event["overall_complete"] is False
    assert event["operator_request"] is delivered
    if delivered:
        # Shown under 交付成果 as a stage result; the goal itself stays open.
        assert event["delivery"]["kind"] == "stage_package"
        assert event["delivery"]["primary_target"]["path"] == "survey-zh.md"
    else:
        assert event["delivery"] is None
