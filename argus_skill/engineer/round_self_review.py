"""Round-loop progress bookkeeping before independent review."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from .round_state import EngineerTurnOutcome, RoundControl, RoundLoopState, control_proceed
from .round_stop_signals import _runner_result_has_successful_work_signal

if TYPE_CHECKING:
    from .runner import SupervisedConfig


class RoundSelfReviewMixin:
    """Update progress state and always continue to the independent Reviewer."""

    def _handle_progress_and_self_review(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        review_completed_hook,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        _ = round_index, supervised_config, review_completed_hook, on_event
        state.backend_failure_streak = 0
        if _runner_result_has_successful_work_signal(
            outcome.engineer_result,
            engineer_message=outcome.engineer_message,
        ):
            state.no_progress_streak = 0
        else:
            state.no_progress_streak += 1
        return control_proceed()


__all__ = ["RoundSelfReviewMixin"]
