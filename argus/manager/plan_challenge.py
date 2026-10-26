"""Manager-owned routing for evidence-backed mission challenges.

This module does not judge whether the Reviewer's technical claim is true. A
challenge with a concrete alternative may replace the Planner's working plan;
any other revises it. The Planner still inspects the evidence and authors any
replacement DAG.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanChallengeDecision:
    action: str  # keep | revise | replace
    reason: str
    challenge: str = ""
    alternative: str = ""
    source: str = "manager_authority_policy"


def adjudicate_plan_challenge(
    *,
    reviewer_status: str = "",
    review_reason: str = "",
    next_action: str = "",
) -> PlanChallengeDecision:
    """Route a Reviewer challenge without promoting Planner prose to authority."""
    status = str(reviewer_status or "").strip().lower()
    challenge = str(review_reason or "").strip()
    alternative = str(next_action or "").strip()

    if status != "planner":
        return PlanChallengeDecision(
            action="keep",
            reason="Reviewer did not challenge the current plan",
            challenge=challenge,
            alternative=alternative,
        )
    if alternative:
        return PlanChallengeDecision(
            action="replace",
            reason="Later evidence supports a concrete alternative to the current plan",
            challenge=challenge,
            alternative=alternative,
        )
    return PlanChallengeDecision(
        action="revise",
        reason="Later evidence materially challenges the current plan",
        challenge=challenge,
    )


__all__ = ["PlanChallengeDecision", "adjudicate_plan_challenge"]
