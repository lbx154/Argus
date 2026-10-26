"""Round and task statuses older releases wrote load as the holder they named."""
from __future__ import annotations

import pytest

from argus.core.handoff import holder_of
from argus.core.stop_kinds import park_reason_of
from argus.life.mission_outcome import mission_outcome_dimensions, outcome_dimension_summary


@pytest.mark.parametrize(("old", "question", "holder"), [
    ("continue", "", "engineer"),
    ("replan_requested", "", "planner"),
    ("blocked", "", "manager"),
    ("blocked", "A or B?", "operator"),
    ("research_incomplete", "", "engineer"),
    ("paused_no_breakthrough", "", "planner"),
    ("exhausted_current_methods", "", "planner"),
    ("Continue", "", "engineer"),
])
def test_an_older_round_status_reads_as_its_holder(old: str, question: str, holder: str) -> None:
    assert holder_of(old, question) == holder


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
