"""Apply a task-scoped Manager WAIT only between completed role operations."""
from __future__ import annotations

from .round_config import SupervisedConfig
from .round_state import RoundLoopState, TerminalResult


def manager_wait_terminal(config: SupervisedConfig, state: RoundLoopState) -> TerminalResult | None:
    from ..manager.supervision import mission_wait_reason

    reason = mission_wait_reason(config.operator_question_policy_root, config.session_id)
    if not reason:
        return None
    # Preserve the actual Engineer and Reviewer history. This is a Manager
    # control boundary, so it must not manufacture a Reviewer verdict/round.
    return (
        "paused_operator", state.rounds, state.last_engineer_message, reason,
        state.engineer_session.thread_id or None if state.engineer_session else None,
    )
