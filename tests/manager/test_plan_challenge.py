from __future__ import annotations

from argus.manager.plan_challenge import adjudicate_plan_challenge


def test_done_verdict_keeps_the_plan() -> None:
    decision = adjudicate_plan_challenge(
        reviewer_status="done",
        review_reason="The local candidate is complete but no longer preferred.",
        next_action="Use the no-gap validator alternative.",
    )

    assert decision.action == "keep"
    assert "no-gap" in decision.alternative


def test_explicit_replan_with_alternative_replaces_working_plan() -> None:
    decision = adjudicate_plan_challenge(
        reviewer_status="replan_requested",
        review_reason="The preselected skip-zero candidate is not required.",
        next_action="Use the no-gap validator alternative.",
    )

    assert decision.action == "replace"
    assert "no-gap" in decision.alternative


def test_unchallenged_plan_is_kept() -> None:
    assert adjudicate_plan_challenge(reviewer_status="continue").action == "keep"
