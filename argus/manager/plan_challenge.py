"""Manager-owned routing for evidence-backed mission challenges.

This module does not judge whether the Reviewer's technical claim is true. It
keeps the authority boundary explicit: operator-owned changes go back to the
operator, while technical alternatives may revise or replace the Planner's
working plan. The Planner still inspects the evidence and authors any replacement
DAG.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

_AUTHORITY_IMPACTS = frozenset({"technical", "manager_contract", "operator"})


@dataclass(frozen=True)
class PlanChallengeDecision:
    action: str  # keep | revise | replace | ask_operator
    reason: str
    challenge: str = ""
    alternative: str = ""
    authority_impact: str = "technical"
    source: str = "manager_authority_policy"


def adjudicate_plan_challenge(
    planner_report: Mapping[str, Any] | None,
    *,
    reviewer_status: str = "",
    review_reason: str = "",
    next_action: str = "",
) -> PlanChallengeDecision:
    """Route a Reviewer challenge without promoting Planner prose to authority."""
    report = planner_report if isinstance(planner_report, Mapping) else {}
    status = str(reviewer_status or "").strip().lower()
    challenge = str(report.get("challenge") or review_reason or "").strip()
    alternative = str(report.get("alternative") or next_action or "").strip()
    authority = str(report.get("authority_impact") or "technical").strip().lower()
    if authority not in _AUTHORITY_IMPACTS:
        authority = "technical"

    if status != "replan_requested":
        return PlanChallengeDecision(
            action="keep",
            reason="Reviewer did not challenge the current plan",
            challenge=challenge,
            alternative=alternative,
            authority_impact=authority,
        )
    if authority == "operator":
        # The Reviewer's own label decides; words in the challenge never do.
        return PlanChallengeDecision(
            action="ask_operator",
            reason="Reviewer marked an operator-owned decision",
            challenge=challenge,
            alternative=alternative,
            authority_impact="operator",
        )
    if alternative:
        return PlanChallengeDecision(
            action="replace",
            reason="Later evidence supports a concrete alternative to the current plan",
            challenge=challenge,
            alternative=alternative,
            authority_impact=authority,
        )
    return PlanChallengeDecision(
        action="revise",
        reason="Later evidence materially challenges the current plan",
        challenge=challenge,
        authority_impact=authority,
    )


__all__ = ["PlanChallengeDecision", "adjudicate_plan_challenge"]
