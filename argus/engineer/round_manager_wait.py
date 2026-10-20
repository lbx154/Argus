"""Apply a task-scoped Manager WAIT or STOP only between completed role operations."""
from __future__ import annotations

from .round_config import SupervisedConfig
from .round_state import RoundLoopState, TerminalResult


def manager_wait_terminal(config: SupervisedConfig, state: RoundLoopState) -> TerminalResult | None:
    from ..manager.supervision import mission_stop_reason, mission_wait_reason

    root, mission = config.operator_question_policy_root, config.session_id
    status, reason = "paused_operator", mission_wait_reason(root, mission)
    if not reason:
        # A stopped run parks where the Planner can retire it or the operator
        # resume it; the workspace stays as the rounds left it.
        status, reason = "paused_no_breakthrough", mission_stop_reason(root, mission)
    if not reason:
        return None
    # Preserve the actual Engineer and Reviewer history. This is a Manager
    # control boundary, so it must not manufacture a Reviewer verdict/round.
    return (
        status, state.rounds, state.last_engineer_message, reason,
        state.engineer_session.thread_id or None if state.engineer_session else None,
    )
