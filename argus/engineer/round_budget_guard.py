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
from pathlib import Path

from .round_config import SupervisedConfig
from .round_state import RoundLoopState, TerminalResult

log = logging.getLogger(__name__)
_UNENFORCEABLE_LOGGED: set[str] = set()


def _ledger_root(config: SupervisedConfig) -> Path | None:
    # The project state directory holds usage.jsonl; the engineer log lives
    # beside it. The operator-context root is the same directory unless an
    # explicit operator-context directory was configured.
    if str(config.engineer_log_path or "").strip():
        return Path(config.engineer_log_path).expanduser().parent
    return config.operator_question_policy_root


def mission_budget_terminal(config: SupervisedConfig, state: RoundLoopState) -> TerminalResult | None:
    from ..core.budget_signal import (
        MISSION_BUDGET_REASON_PREFIX,
        mission_budget,
        mission_budget_reached,
        mission_usage_summary,
        record_mission_budget_pause,
    )

    budget = mission_budget()
    if not budget.enabled:
        return None
    item_id = str(config.session_id or "")
    root = _ledger_root(config)
    if root is None or not item_id:
        key = item_id or "(no mission id)"
        if key not in _UNENFORCEABLE_LOGGED:
            _UNENFORCEABLE_LOGGED.add(key)
            log.warning(
                "per-mission budget is set but cannot be enforced for %s: no project "
                "state directory (ARGUS_SKILL_CHECKPOINT_PERSIST is off?)", key,
            )
        return None
    try:
        summary = mission_usage_summary(root, item_id)
        reached = mission_budget_reached(summary, budget)
    except Exception:  # noqa: BLE001 - an unreadable ledger must not stop work
        log.warning("mission budget check failed for %s", item_id, exc_info=True)
        return None
    if not reached:
        return None
    try:
        record_mission_budget_pause(root, item_id, reached=reached, summary=summary, budget=budget)
    except OSError:
        # The reason below identifies the pause on its own; the marker only
        # carries the spend figures for the operator's card.
        log.warning("could not record mission budget pause for %s", item_id, exc_info=True)
    reason = f"{MISSION_BUDGET_REASON_PREFIX} {reached}."
    return (
        "paused_operator", state.rounds, state.last_engineer_message, reason,
        state.engineer_session.thread_id or None if state.engineer_session else None,
    )
