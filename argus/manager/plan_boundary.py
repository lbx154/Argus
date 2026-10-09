"""The plan-challenge boundary check, exactly as on dev.

This PR changes how *questions* are routed (the raising role classifies them)
but deliberately leaves plan-challenge routing as it was: a Reviewer replan
and its proposed alternative go through this check, unchanged from dev,
including its narrow text match over the challenge and the alternative.
Replacing it with a Manager label plus command-form defense in depth is
follow-up work. Kept separate from ``core.autonomy`` so question routing
never reaches it.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..core.autonomy import (
    OperatorIntervention,
    normalize_autonomy_mode,
    resolve_autonomy_mode,
)

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



def assess_plan_boundary(
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



__all__ = ["assess_plan_boundary"]
