from __future__ import annotations

from argus_skill.core.models import LoopOutcome, ReviewDecision, RoundRecord
from argus_skill.life.supervisor._planning_cycle_helpers import _revision_reason


def test_revision_reason_falls_back_to_stable_nonempty_value() -> None:
    assert _revision_reason({}) == "Reviewer requested replacement of the active plan"
    assert _revision_reason({"reason": "replace stale node"}) == "replace stale node"


def test_loop_outcome_exposes_final_reviewer_reason() -> None:
    review = ReviewDecision(
        status="replan_requested",
        reason="the active node is refuted",
        next_action="replace it",
    )
    outcome = LoopOutcome(
        status="replan_requested",
        rounds=[RoundRecord(1, "", 0, review)],
        final_message="",
        reason="",
        workdir="/tmp",
    )

    assert outcome.final_review_reason == "the active node is refuted"
