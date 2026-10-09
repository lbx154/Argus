"""Turn a reached per-mission budget into an operator decision.

The round loop stops at a round boundary once the operator's per-mission
budget is spent and leaves a marker. Here that stop becomes a pending
question: the mission is parked as ``paused_operator`` with a decision card,
the operator is notified on every channel a question would use, and nothing
continues until the operator answers. Argus never decides on its own to drop
or continue the work.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.event_catalog import EventType
from ..mission_outcome import mission_outcome_class, mission_outcome_dimensions

if TYPE_CHECKING:
    from ._mission_execution_helpers import _MissionRunState


def _question(marker: dict[str, Any], *, chinese: bool) -> str:
    raw_spent = marker.get("spent")
    raw_budget = marker.get("budget")
    spent: dict[str, Any] = raw_spent if isinstance(raw_spent, dict) else {}
    budget: dict[str, Any] = raw_budget if isinstance(raw_budget, dict) else {}
    requests = float(spent.get("premium_requests") or 0.0)
    credits = float(spent.get("credits") or 0.0)
    usd = float(spent.get("known_cost_usd") or 0.0)
    calls = int(spent.get("calls") or 0)
    limits_en: list[str] = []
    limits_zh: list[str] = []
    if float(budget.get("requests") or 0) > 0:
        limits_en.append(f"{float(budget['requests']):g} premium requests")
        limits_zh.append(f"{float(budget['requests']):g} 次高级请求")
    if float(budget.get("usd") or 0) > 0:
        limits_en.append(f"${float(budget['usd']):.2f}")
        limits_zh.append(f"${float(budget['usd']):.2f}")
    if chinese:
        spent_text = f"{calls} 次模型调用、{requests:g} 次高级请求"
        if credits:
            spent_text += f"、{credits:,.1f} 点额度"
        if usd:
            spent_text += f"（约 ${usd:.2f}）"
        return (
            f"这个任务已用掉 {spent_text}，达到你设的单任务预算（{' / '.join(limits_zh)}）。"
            "我先停在这里，没有再花钱。选“继续”会按同样的预算再走一段；"
            "选“停止”会结束这个任务并暂停持续工作。"
        )
    spent_text = f"{calls} model calls, {requests:g} premium requests"
    if credits:
        spent_text += f", {credits:,.1f} credits"
    if usd:
        spent_text += f" (about ${usd:.2f})"
    return (
        f"This task has used {spent_text}, reaching the per-mission budget you set "
        f"({' / '.join(limits_en)}). I have stopped here and am not spending more. "
        "Choose Continue to go on with the same budget again, or Stop to close this "
        "task and pause the campaign."
    )


def mission_budget_card(
    item: Any, marker: dict[str, Any], *, project_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """The operator question and decision card for a reached mission budget."""
    from ...core.budget_signal import MISSION_BUDGET_DECISION_KIND, MISSION_BUDGET_REASON_PREFIX
    from ...core.operator_decision import build_operator_decision
    from ...core.operator_messages import uses_cjk

    chinese = uses_cjk(f"{item.title}\n{item.objective}")
    question = _question(marker, chinese=chinese)
    reason = f"{MISSION_BUDGET_REASON_PREFIX} {marker.get('reached') or ''}".strip()
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason=reason,
        question=question,
        options=[
            {
                "id": "resume",
                "label": "继续" if chinese else "Continue",
                "description": (
                    "按同样的单任务预算继续这个任务。" if chinese
                    else "Continue this task with the same per-mission budget again."
                ),
            },
            {
                # "stop" is the option the decision resolver applies directly:
                # the task is closed and the standing campaign is paused, with
                # no new work queued.
                "id": "stop",
                "label": "停止" if chinese else "Stop",
                "description": (
                    "结束这个任务并暂停持续工作，不再花费；已有成果保留。" if chinese
                    else "Close this task and pause the standing campaign; nothing more is "
                    "spent and current work is kept."
                ),
            },
        ],
        evidence=list(getattr(item, "context_refs", None) or []),
        project_id=project_id,
        previous_decision=item.operator_decision,
    )
    card["decision_kind"] = MISSION_BUDGET_DECISION_KIND
    card["spend"] = marker.get("spent") or {}
    card["budget"] = marker.get("budget") or {}
    return question, card


def park_for_mission_budget(runtime: Any, state: "_MissionRunState") -> dict[str, Any] | None:
    """Park ``state.item`` for the operator when the round loop hit its budget."""
    from ...core.budget_signal import (
        MISSION_BUDGET_DECISION_KIND,
        MISSION_BUDGET_REASON_PREFIX,
        compact_usage,
        mission_budget,
        mission_usage_summary,
        take_mission_budget_pause,
    )
    from .pending_notify import notify_pending_question

    item = state.item
    # The project's state directory: where the round loop's ledger and marker
    # live (memory.project_root, falling back to the bundle root).
    project_root = getattr(state, "usage_root", None)
    if project_root is None:
        return None
    # Always consume this item's marker so a leftover one can never turn a
    # later Manager WAIT into a budget card.
    marker = take_mission_budget_pause(project_root, item.id)
    if not str(getattr(state, "stop_reason", "") or "").startswith(MISSION_BUDGET_REASON_PREFIX):
        return None
    started = float(getattr(state, "t0", 0.0) or 0.0)
    if marker is not None and float(marker.get("at") or 0.0) < started:
        marker = None  # written by an earlier attempt
    current = next((row for row in runtime.memory.backlog.active() if row.id == item.id), None)
    if current is None or current.status != "running":
        # Stopped or answered meanwhile; the operator's newer state wins.
        return None
    if marker is None:
        # The pause is identified by its reason; rebuild the figures.
        budget = mission_budget()
        summary = mission_usage_summary(project_root, item.id)
        marker = {
            "reached": str(state.stop_reason)[len(MISSION_BUDGET_REASON_PREFIX):].strip(" ."),
            "budget": budget.to_jsonable(),
            "spent": compact_usage(summary),
        }
    question, card = mission_budget_card(item, marker, project_id=Path(project_root).name)
    reason = f"{MISSION_BUDGET_REASON_PREFIX} {marker.get('reached') or ''}".strip()
    usage_summary = state.usage_summary
    pause_outcome = mission_outcome_dimensions(
        status="paused_operator",
        success=False,
        review_status=str(getattr(state.outcome, "final_review_status", "") or ""),
        stop_kind=None,
        resumable=True,
    )
    runtime.memory.backlog.update(
        item.id,
        status="paused_operator",
        finished_ts=time.time(),
        last_error=reason,
        outcome=pause_outcome,
        pending_question=question,
        operator_decision=card,
    )
    item.pending_question = question
    item.operator_decision = card
    notify_pending_question(project_root, item)
    runtime._emit({
        "type": EventType.LIFE_OPERATOR_QUESTION_PENDING,
        "item_id": item.id,
        "title": item.title,
        "question": question,
        "agent_layer": "manager",
        "reason": MISSION_BUDGET_DECISION_KIND,
    })
    pricing_status = usage_summary.pricing_status if usage_summary is not None else ""
    runtime._emit({
        "type": EventType.LIFE_MISSION_COMPLETED,
        "item_id": item.id,
        "success": False,
        "status": "paused_operator",
        "outcome_class": mission_outcome_class(status="paused_operator", success=False),
        "outcome": pause_outcome,
        "stop_kind": None,
        "stop_reason": reason,
        "recoverable": True,
        "cost_usd": state.usd,
        "known_cost_usd": state.known_usd,
        "pricing_status": pricing_status,
        "spent_usd": state.known_usd,
    })
    return {
        "status": "paused_operator",
        "item_id": item.id,
        "success": False,
        "stop_kind": None,
        "recoverable": True,
        "cost_usd": state.usd,
        "known_cost_usd": state.known_usd,
        "pricing_status": pricing_status,
    }


__all__ = ["mission_budget_card", "park_for_mission_budget"]
