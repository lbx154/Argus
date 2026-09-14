"""Pragmatic operator-intervention policy.

Argus should ask a person for choices that only a person can make, not for
recoverable engineering decisions.  This module keeps that boundary small and
explicit: credentials, money, irreversible/outward-facing actions, and changes
to an operator-owned acceptance contract require the operator; technical route
selection and reversible diagnostics stay with Argus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

AUTONOMY_MODES = frozenset({"cautious", "pragmatic", "autonomous"})

# These are authority boundaries, not a generic list of scary technical words.
# Keep them narrow so an ordinary timeout, failed test, or unavailable backend
# does not become a human interrupt.
_OPERATOR_BOUNDARY_RE = re.compile(
    r"(?:"
    r"credential|api[ _-]?key|access[ _-]?token|password|secret|login|sign[ -]?in|"
    r"payment|purchase|billing|increase (?:the )?budget|spend more|paid|"
    r"delete (?:operator|user|production)|drop (?:operator|user|production)|"
    r"force[ -]?push|publish|release publicly|send externally|"
    r"trusted boundary|security boundary|legal approval|license approval|"
    r"is .{0,80} acceptable|may i|am i allowed|must (?:we|this)|"
    r"凭证|密钥|令牌|密码|登录|付款|购买|账单|增加预算|额外预算|"
    r"删除(?:用户|生产|正式)|强制推送|公开发布|对外发送|"
    r"信任边界|安全边界|法律批准|许可证批准|"
    r"可以接受吗|是否可接受|是否允许|能否授权|必须保留|必须使用"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class OperatorIntervention:
    required: bool
    mode: str
    reason: str
    authority_impact: str


def normalize_autonomy_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in AUTONOMY_MODES else "pragmatic"


def resolve_autonomy_mode(*, env: Mapping[str, str] | None = None) -> str:
    """Resolve the operator-facing autonomy mode through the normal knob layer."""
    from .knobs import resolve_knob

    return normalize_autonomy_mode(
        resolve_knob(
            "ARGUS_SKILL_AUTONOMY_MODE",
            "pragmatic",
            env=env,
        ).value
    )


def assess_operator_intervention(
    *,
    question: str,
    reason: str = "",
    next_action: str = "",
    planner_report: Mapping[str, Any] | None = None,
    mode: str | None = None,
) -> OperatorIntervention:
    """Decide whether a blocked/replan question truly needs a person.

    ``authority_impact`` is the primary structured signal.  The narrow text
    fallback keeps older Reviewer outputs useful without turning every technical
    failure into a pause.  ``autonomous`` still stops at credentials, money,
    irreversible actions, and operator-owned acceptance boundaries.
    """
    selected_mode = normalize_autonomy_mode(mode or resolve_autonomy_mode())
    report = planner_report if isinstance(planner_report, Mapping) else {}
    authority = str(report.get("authority_impact") or "").strip().lower()
    text = "\n".join((str(question or ""), str(reason or ""), str(next_action or "")))
    hard_boundary = bool(_OPERATOR_BOUNDARY_RE.search(text))

    if not str(question or "").strip():
        return OperatorIntervention(False, selected_mode, "no operator question", authority)
    if selected_mode == "cautious":
        return OperatorIntervention(True, selected_mode, "cautious mode asks on every explicit question", authority)
    if hard_boundary:
        return OperatorIntervention(True, selected_mode, "question crosses an operator authority boundary", authority)
    if authority == "operator":
        return OperatorIntervention(True, selected_mode, "Reviewer marked an operator-owned decision", authority)
    if authority in {"technical", "manager_contract"}:
        return OperatorIntervention(False, selected_mode, "technical or Manager-owned choice is recoverable", authority)
    return OperatorIntervention(False, selected_mode, "reversible technical choice stays with Argus", authority)


def technical_continuation(
    *,
    question: str,
    reason: str = "",
    next_action: str = "",
) -> str:
    """Turn a non-operator blocker into a concrete Planner instruction."""
    action = str(next_action or "").strip()
    if action:
        return action
    why = str(reason or question or "the current route stalled").strip()
    return (
        "Replan this as a reversible technical problem. Diagnose the current "
        f"failure ({why}), try the smallest informative check first, and choose "
        "a different in-scope route without waiting for operator confirmation."
    )


__all__ = [
    "AUTONOMY_MODES",
    "OperatorIntervention",
    "assess_operator_intervention",
    "normalize_autonomy_mode",
    "resolve_autonomy_mode",
    "technical_continuation",
]
