"""Pause at a round boundary when the operator's per-mission budget is reached.

Off by default: with neither ``ARGUS_SKILL_MISSION_BUDGET_REQUESTS`` nor
``ARGUS_SKILL_MISSION_BUDGET_USD`` set, this never reads the ledger and never
pauses. When the operator did set one, the mission stops between role calls
(never inside one) and the supervisor turns the pause into an operator
decision: the operator chooses whether to continue. Argus does not judge the
work's content here; the limit is the operator's.
"""
from __future__ import annotations

import logging

from .round_config import SupervisedConfig
from .round_state import RoundLoopState, TerminalResult

log = logging.getLogger(__name__)


def mission_budget_terminal(config: SupervisedConfig, state: RoundLoopState) -> TerminalResult | None:
    from ..core.budget_signal import (
        mission_budget,
        mission_budget_reached,
        mission_usage_summary,
        record_mission_budget_pause,
    )

    budget = mission_budget()
    if not budget.enabled:
        return None
    root = config.operator_question_policy_root
    item_id = str(config.session_id or "")
    if root is None or not item_id:
        return None
    try:
        summary = mission_usage_summary(root, item_id)
        reached = mission_budget_reached(summary, budget)
        if not reached:
            return None
        record_mission_budget_pause(root, item_id, reached=reached, summary=summary, budget=budget)
    except Exception:  # noqa: BLE001 - an unreadable ledger must not stop work
        log.warning("mission budget check failed for %s", item_id, exc_info=True)
        return None
    reason = f"This mission reached the operator's per-mission budget: {reached}."
    return (
        "paused_operator", state.rounds, state.last_engineer_message, reason,
        state.engineer_session.thread_id or None if state.engineer_session else None,
    )
