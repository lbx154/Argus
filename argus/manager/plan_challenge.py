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
    operator_need: str = ""


def adjudicate_plan_challenge(
    planner_report: Mapping[str, Any] | None,
    *,
    reviewer_status: str = "",
    review_reason: str = "",
    next_action: str = "",
    operator_question: str = "",
    alternative_operator_need: str | None = None,
    workspace: Any = None,
) -> PlanChallengeDecision:
    """Route a Reviewer challenge without promoting Planner prose to authority.

    ``alternative_operator_need`` is the Manager's own classification of what
    carrying out the proposed alternative needs (a label, ``"none"``, or
    missing). See :func:`route_plan_alternative`.
    """
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
    if authority == "operator" or str(operator_question or "").strip():
        from ..core.autonomy import assess_operator_intervention

        intervention = assess_operator_intervention(
            question=(
                str(operator_question or "").strip()
                or challenge
                or "Please decide this operator-owned constraint."
            ),
            reason=challenge,
            next_action=alternative,
            planner_report={
                "authority_impact": authority,
                "operator_need": report.get("operator_need") or "",
            },
        )
        if intervention.required:
            return PlanChallengeDecision(
                action="ask_operator",
                reason=intervention.reason,
                challenge=challenge,
                alternative=alternative,
                authority_impact="operator",
                operator_need=intervention.operator_need,
            )
    if alternative:
        routed = route_plan_alternative(
            alternative,
            alternative_operator_need,
            workspace=workspace,
        )
        if routed is not None:
            action, need, source = routed
            return PlanChallengeDecision(
                action=action,
                reason=(
                    "Carrying out the proposed alternative needs the operator"
                    + (f" ({need})" if need else " (the Manager could not classify it)")
                    + (
                        "; it is held for the operator"
                        if action == "ask_operator"
                        else "; no operator is available, so the mission is blocked"
                    )
                ),
                challenge=challenge,
                alternative=alternative,
                authority_impact="operator",
                source=source,
                operator_need=need,
            )
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


def route_plan_alternative(
    alternative: str,
    manager_operator_need: str | None,
    *,
    workspace: Any = None,
) -> tuple[str, str, str] | None:
    """Decide whether a proposed alternative may replace the plan.

    Two layers. The primary one is judgement: the Manager labels what carrying
    out the alternative needs. With an operator available, any label other
    than ``none`` (or no label at all) goes to the operator; without one, a
    label means blocked. Behind it, as defense in depth, exact command forms
    (:func:`core.autonomy.operator_only_command`) always count, whatever the
    label says. Returns ``(action, operator_need, source)`` or ``None`` when
    the alternative may replace the plan.
    """
    from ..core.autonomy import (
        NO_OPERATOR_NEED,
        OPERATOR_NEEDS,
        normalize_operator_need,
        operator_available,
        operator_only_command,
    )

    label = normalize_operator_need(manager_operator_need)
    command_need = operator_only_command(alternative, workspace=workspace)
    if label in OPERATOR_NEEDS:
        need, source = label, "manager_alternative_classification"
    elif command_need:
        need, source = command_need, "operator_only_command"
    else:
        need, source = "", "manager_alternative_unclassified"
    if operator_available():
        if label == NO_OPERATOR_NEED and not command_need:
            return None
        return ("ask_operator", need, source)
    if need:
        return ("blocked", need, source)
    return None


__all__ = [
    "PlanChallengeDecision",
    "adjudicate_plan_challenge",
    "route_plan_alternative",
]
