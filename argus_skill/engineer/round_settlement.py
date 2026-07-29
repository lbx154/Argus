"""Settle a round from the Reviewer's minimal verdict."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import LoopStatus, ReviewDecision, RoundRecord
from .round_signals import _review_event_payload
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_proceed,
    control_return,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


class RoundSettlementMixin:
    """Mixin providing ``SupervisedEngineer``'s round-settlement phase and
    the ``_classify`` terminal-status decision gate."""

    @staticmethod
    def _classify(
        *,
        review: ReviewDecision,
        no_progress_streak: int,
        no_progress_threshold: int,
        semantic_stall_streak: int = 0,
        stall_threshold: int = 0,
        round_index: int,
        max_rounds: int,
        hard_escalate_rounds: int = 0,
        decision_idle_seconds: float = 0.0,
        decision_timeout_seconds: int = 0,
    ) -> tuple[LoopStatus | None, str]:
        if review.status == "done":
            return "done", review.reason or "Reviewer judged the objective complete."
        if review.status == "blocked":
            if review.backend_unavailable and not review.operator_question:
                return "infra_blocked", review.reason or "Research infrastructure blocked progress."
            return "blocked", review.reason or "Reviewer blocked progress."
        if review.status == "replan_requested":
            return (
                "replan_requested",
                review.reason or "Reviewer requested a Manager-owned replacement plan.",
            )
        if no_progress_streak >= no_progress_threshold:
            return (
                "no_progress",
                "Engineer produced no effective output for "
                f"{no_progress_streak} consecutive rounds.",
            )
        if (
            stall_threshold > 0
            and semantic_stall_streak >= stall_threshold
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                "Reviewer reported no decision progress for "
                f"{semantic_stall_streak} consecutive rounds.",
            )
        if (
            decision_timeout_seconds > 0
            and decision_idle_seconds >= decision_timeout_seconds
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                f"Reached {decision_timeout_seconds} seconds without decision progress.",
            )
        if (
            hard_escalate_rounds > 0
            and round_index >= hard_escalate_rounds
            and review.status == "continue"
        ):
            return (
                "blocked",
                f"Escalated: ran {round_index} rounds without completing — the "
                "mission is likely stuck on an external / unresolved constraint. "
                "Ending so the planner can re-plan or decompose. " + (review.reason or ""),
            )
        return None, ""

    def _settle_round(
        self,
        *,
        review: ReviewDecision,
        round_index: int,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        review_completed_hook,
        continue_adaptor,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        engineer_result = outcome.engineer_result
        engineer_message = outcome.engineer_message
        state.reviewer_backend_failure_streak = 0
        state.pending_secret_guard_notes.clear()
        now_monotonic = time.monotonic()
        state.last_decision_progress_at = now_monotonic
        if on_event:
            on_event(
                _review_event_payload(
                    review,
                    round_index=round_index,
                    round_max=supervised_config.max_rounds,
                    text=f"review: {review.status} — {review.reason}",
                )
            )
        record = RoundRecord(
            round_index=round_index,
            engineer_message=engineer_message,
            engineer_exit_code=engineer_result.exit_code,
            review=review,
            fatal_error=engineer_result.fatal_error,
        )
        state.rounds.append(record)
        state.reviewer_next_action = review.next_action if review.status == "continue" else None
        if review_completed_hook is not None:
            try:
                review_completed_hook(record)
            except Exception:  # noqa: BLE001 - memory capture never owns verdict
                log.warning("review completion hook failed", exc_info=True)

        state.semantic_stall_streak = 0
        terminal_status, reason = self._classify(
            review=review,
            no_progress_streak=state.no_progress_streak,
            no_progress_threshold=supervised_config.no_progress_threshold,
            semantic_stall_streak=state.semantic_stall_streak,
            stall_threshold=supervised_config.stall_threshold,
            round_index=round_index,
            max_rounds=supervised_config.max_rounds,
            hard_escalate_rounds=supervised_config.hard_escalate_rounds,
            decision_idle_seconds=0.0,
            decision_timeout_seconds=(supervised_config.decision_progress_timeout_seconds),
        )
        if terminal_status is not None:
            return control_return(
                (
                    terminal_status,
                    state.rounds,
                    state.last_engineer_message,
                    reason,
                    None,
                )
            )

        if continue_adaptor is not None:
            try:
                adapted = str(continue_adaptor(state.rounds) or "").strip()
                if adapted:
                    prior = str(state.reviewer_next_action or "").strip()
                    state.reviewer_next_action = (
                        "## Scientist alternative playbook for the next round\n"
                        + adapted
                        + (
                            "\n\n## Reviewer guidance\n" + prior
                            if prior
                            else ""
                        )
                    )
            except Exception:  # noqa: BLE001 — adaptation is advisory
                log.debug("continue adaptor failed", exc_info=True)
        return control_proceed()
