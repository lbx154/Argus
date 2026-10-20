from __future__ import annotations

from argus.manager.plan_challenge import adjudicate_plan_challenge


def test_done_verdict_keeps_plan_signal_advisory() -> None:
    decision = adjudicate_plan_challenge(
        {
            "plan_signal": "reconsider",
            "challenge": "The preselected skip-zero candidate is not required.",
            "alternative": "Use the no-gap validator alternative.",
            "authority_impact": "technical",
        },
        reviewer_status="done",
        review_reason="The local candidate is complete but no longer preferred.",
    )

    assert decision.action == "keep"
    assert decision.authority_impact == "technical"
    assert "no-gap" in decision.alternative


def test_explicit_replan_with_alternative_replaces_working_plan() -> None:
    decision = adjudicate_plan_challenge(
        {
            "plan_signal": "reconsider",
            "challenge": "The preselected skip-zero candidate is not required.",
            "alternative": "Use the no-gap validator alternative.",
            "authority_impact": "technical",
        },
        reviewer_status="replan_requested",
    )

    assert decision.action == "replace"
    assert "no-gap" in decision.alternative


def test_technical_question_uses_the_available_alternative() -> None:
    decision = adjudicate_plan_challenge(
        {
            "plan_signal": "reconsider",
            "challenge": "The largest benchmark row timed out.",
            "alternative": "Run the isolated one-row diagnostic first.",
            "authority_impact": "technical",
        },
        reviewer_status="replan_requested",
    )

    assert decision.action == "replace"
    assert decision.authority_impact == "technical"


def test_unchallenged_plan_is_kept() -> None:
    decision = adjudicate_plan_challenge(
        {"plan_signal": "continue"},
        reviewer_status="continue",
    )

    assert decision.action == "keep"
