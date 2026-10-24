from __future__ import annotations

from argus.core.models import ReviewDecision


def test_done_is_the_reviewers_completion_judgment() -> None:
    review = ReviewDecision(
        status="done",
        reason="The requested result is complete and supported by the inspected evidence.",
        next_action="",
    )

    assert review.final_submission_certified is True


def test_non_done_does_not_certify_completion() -> None:
    for status in ("continue", "blocked", "replan_requested"):
        review = ReviewDecision(status=status, reason="Not complete.", next_action="Act.")
        assert review.final_submission_certified is False
