"""Round and task statuses older releases wrote load as the holder they named."""
from __future__ import annotations

import pytest

from argus.core.handoff import holder_of
from argus.core.stop_kinds import park_reason_of
from argus.life.mission_outcome import mission_outcome_dimensions, outcome_dimension_summary


@pytest.mark.parametrize(("old", "question", "holder"), [
    ("continue", "", "engineer"),
    ("replan_requested", "", "planner"),
    ("blocked", "A or B?", "operator"),
    ("Continue", "", "engineer"),
])
def test_an_older_round_status_reads_as_its_holder(old: str, question: str, holder: str) -> None:
    assert holder_of(old, question) == holder


@pytest.mark.parametrize("status", [
    # The host stopped the round, or refused the task: nobody was handed the work.
    "blocked",
    # Older research verdicts are parks (core.stop_kinds), not a request for a new plan.
    "research_incomplete", "paused_no_breakthrough", "exhausted_current_methods",
])
def test_a_status_that_names_no_holder_reads_as_itself(status: str) -> None:
    assert holder_of(status) == status


def test_a_host_stop_hands_the_work_to_nobody() -> None:
    """A budget pause, a model-service outage or a shutdown is recorded, not a
    handoff: the task's outcome says nothing was judged, never "the Manager"."""
    from argus.engineer import round_stop_signals as stops

    records = [
        stops.external_pause_review_decision(stop_kind="budget_exhausted", fatal_error="", exit_code=0),
        stops.execution_host_review_decision(fatal_error="no host", exit_code=1),
        stops.model_configuration_review_decision(fatal_error="no model", exit_code=1),
        stops.daemon_stop_review_decision(fatal_error="stop", exit_code=1, chinese=False),
        stops.operator_abort_review_decision(fatal_error="abort", exit_code=1),
    ]
    for record in records:
        assert record.status == "blocked"
        assert mission_outcome_dimensions(
            status="parked", success=False, review_status=record.status, park_reason="budget_exhausted",
        )["review_status"] == "not_assessed"


@pytest.mark.parametrize("holder", ["engineer", "planner", "manager", "operator", "done", "skipped", ""])
def test_a_holder_reads_as_itself(holder: str) -> None:
    assert holder_of(holder) == holder


def test_a_backlog_row_an_older_release_wrote_keeps_its_review_and_its_park() -> None:
    old_row_outcome = {"execution_status": "paused", "review_status": "continue", "resumable": True}

    assert "Review requested another pass" in outcome_dimension_summary(old_row_outcome)
    assert mission_outcome_dimensions(
        status="no_progress", success=False, review_status="continue",
    )["review_status"] == "engineer"
    assert park_reason_of("paused_budget") == "budget_exhausted"
    assert park_reason_of("paused_operator") == "operator_pause"
    assert park_reason_of("parked", "provider_fence") == "provider_fence"


@pytest.mark.parametrize("status", ["manager", "operator"])
def test_work_handed_past_the_team_waits_and_never_fails(status: str) -> None:
    outcome = mission_outcome_dimensions(status=status, success=False, review_status=status)

    assert outcome["execution_status"] not in {"failed", "blocked"}
