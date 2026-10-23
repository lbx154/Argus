"""The Manager's controls a round reads: a task-scoped STOP applied only between
completed role operations, and the supervision directive that answers a stall."""
from __future__ import annotations

from pathlib import Path

from .round_config import SupervisedConfig
from .round_state import RoundLoopState, TerminalResult


def manager_wait_terminal(config: SupervisedConfig, state: RoundLoopState) -> TerminalResult | None:
    from ..manager.supervision import mission_stop_reason

    # A stopped run parks where the Planner can retire it or the operator
    # resume it; the workspace stays as the rounds left it.
    reason = mission_stop_reason(config.state_root, config.session_id)
    if not reason:
        return None
    # Preserve the actual Engineer and Reviewer history. This is a Manager
    # control boundary, so it must not manufacture a Reviewer verdict/round.
    return (
        "manager_stop", state.rounds, state.last_engineer_message, reason,
        state.engineer_session.thread_id or None if state.engineer_session else None,
    )


def manager_direction_revision(root: Path | None) -> str:
    """The revision of the Manager's supervision directive, or "" when there is none."""
    from ..manager.directive import load_active_manager_directive

    directive = load_active_manager_directive(root)
    if directive is None or not directive.source.startswith("manager.supervision"):
        return ""
    return directive.revision
