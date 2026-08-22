"""Settle a round from the Reviewer's minimal verdict."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.event_catalog import EventType
from ..core.models import LoopStatus, ReviewDecision, RoundRecord
from .checkpoint import resolve_shared_checkpoint
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


def _review_forward_progress(review: ReviewDecision) -> bool | None:
    """Return only the Reviewer's explicit structured progress judgment."""
    report = review.planner_report
    if not isinstance(report, dict):
        return None
    value = report.get("forward_progress")
    return value if isinstance(value, bool) else None


def _review_plan_signal(review: ReviewDecision) -> str:
    report = review.planner_report
    if not isinstance(report, dict):
        return ""
    return str(report.get("plan_signal") or "").strip().lower()


def _next_semantic_stall_streak(
    review: ReviewDecision,
    current_streak: int,
) -> tuple[int, bool | None]:
    """Count consecutive explicit no-progress ``continue`` verdicts.

    A missing or malformed ``forward_progress`` value is unknown, not evidence
    of a stall. Resetting on unknown makes this guard fail open and prevents the
    harness from replacing the Reviewer's judgment with an inference of its own.
    """
    forward_progress = _review_forward_progress(review)
    if review.status == "continue" and forward_progress is False:
        return max(0, int(current_streak)) + 1, forward_progress
    return 0, forward_progress


# Half of every failed mission across seven campaigns ended on one of the stall
# counters, and the reason each returned described only the refusal. One
# campaign read it as a verdict on the work: its claim-bearing benchmark run
# stalled, and the next thing the Planner queued was an unrelated question with
# a four-word objective. What a stall measures is the approach, so the reason
# has to point somewhere instead of just stopping.
_STALL_REDIRECT = (
    " The rounds ended; the question did not. Name what these rounds kept "
    "failing to move, then take a different route to the same question — "
    "changing the question is how a campaign loses its paper."
)


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
        if _review_plan_signal(review) == "reconsider":
            report = review.planner_report if isinstance(review.planner_report, dict) else {}
            challenge = str(report.get("challenge") or review.reason or "").strip()
            authority = str(report.get("authority_impact") or "technical").strip()
            if authority == "operator" and review.operator_question:
                return (
                    "blocked",
                    challenge or "The plan challenge requires an operator decision.",
                )
            return (
                "replan_requested",
                challenge or "Later evidence materially challenged the current plan.",
            )
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
                f"{no_progress_streak} consecutive rounds." + _STALL_REDIRECT,
            )
        if (
            stall_threshold > 0
            and semantic_stall_streak >= stall_threshold
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                "Reviewer reported no forward progress for "
                f"{semantic_stall_streak} consecutive rounds." + _STALL_REDIRECT,
            )
        if (
            decision_timeout_seconds > 0
            and decision_idle_seconds >= decision_timeout_seconds
            and round_index < max_rounds
        ):
            return (
                "no_progress",
                f"Reached {decision_timeout_seconds} seconds without decision "
                "progress." + _STALL_REDIRECT,
            )
        if (
            hard_escalate_rounds > 0
            and round_index >= hard_escalate_rounds
            and review.status == "continue"
            and _review_forward_progress(review) is None
        ):
            return (
                "blocked",
                f"Escalated after {round_index} rounds because Reviewer did not "
                "provide an explicit forward-progress judgment at the continuation "
                "boundary. Refusing to continue blindly; Planner can re-plan or "
                "decompose. " + (review.reason or ""),
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
        next_semantic_stall_streak, forward_progress = _next_semantic_stall_streak(
            review,
            state.semantic_stall_streak,
        )
        now_monotonic = time.monotonic()
        next_decision_progress_at = (
            state.last_decision_progress_at
            if review.status == "continue" and forward_progress is False
            else now_monotonic
        )
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
        # Seal the Reviewer half of the round packet, symmetrically with
        # ``record_engineer_handoff`` in round_execution. Sealed before the
        # terminal classification below, because the terminal round is exactly
        # the one whose verdict the campaign-level stage reconciliation needs.
        #
        # Only a genuine independent Reviewer verdict is sealed: the packet
        # declares ``producer_role="reviewer"`` and the supervisor replays it
        # as independent stage evidence, so sealing a self-review here would
        # let the Engineer certify its own stage transition.
        if supervised_config.context_packet_path and str(
            getattr(review, "review_source", "reviewer") or "reviewer"
        ) == "reviewer":
            try:
                from ..life.context_packet import record_reviewed_handoff

                record_reviewed_handoff(
                    mission_context_path=supervised_config.context_packet_path,
                    round_index=round_index,
                    engineer_summary=engineer_message,
                    review=review,
                    checkpoint_path=resolve_shared_checkpoint(
                        supervised_config.checkpoint_path
                    ),
                )
            except Exception:  # noqa: BLE001 - handoff persistence is fail-soft
                log.exception("failed to persist Reviewer context packet")
        if review_completed_hook is not None:
            try:
                review_completed_hook(record)
            except Exception:  # noqa: BLE001 - memory capture never owns verdict
                log.warning("review completion hook failed", exc_info=True)

        state.semantic_stall_streak = next_semantic_stall_streak
        state.last_decision_progress_at = next_decision_progress_at
        decision_idle_seconds = max(
            0.0,
            now_monotonic - state.last_decision_progress_at,
        )
        if on_event and state.semantic_stall_streak > 0:
            on_event(
                {
                    "type": EventType.ROUND_STALL,
                    "round_index": round_index,
                    "round_max": supervised_config.max_rounds,
                    "forward_progress": False,
                    "semantic_stall_streak": state.semantic_stall_streak,
                    "stall_threshold": supervised_config.stall_threshold,
                    "decision_idle_seconds": round(decision_idle_seconds, 1),
                    "text": (
                        "reviewer reported no forward progress "
                        f"{state.semantic_stall_streak}/"
                        f"{supervised_config.stall_threshold} rounds"
                    ),
                }
            )
        if (
            on_event
            and supervised_config.hard_escalate_rounds > 0
            and round_index == supervised_config.hard_escalate_rounds
            and review.status == "continue"
            and forward_progress is not None
        ):
            on_event({
                "type": EventType.ROUND_ESCALATED,
                "round_index": round_index,
                "hard_escalate_rounds": supervised_config.hard_escalate_rounds,
                "forward_progress": forward_progress,
                "continuation_reason": (
                    "semantic_progress"
                    if forward_progress
                    else "bounded_no_progress_observation"
                ),
                "text": (
                    f"round {round_index} crossed the continuation boundary under "
                    "the Reviewer's explicit progress judgment"
                ),
            })
        terminal_status, reason = self._classify(
            review=review,
            no_progress_streak=state.no_progress_streak,
            no_progress_threshold=supervised_config.no_progress_threshold,
            semantic_stall_streak=state.semantic_stall_streak,
            stall_threshold=supervised_config.stall_threshold,
            round_index=round_index,
            max_rounds=supervised_config.max_rounds,
            hard_escalate_rounds=supervised_config.hard_escalate_rounds,
            decision_idle_seconds=decision_idle_seconds,
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

        if continue_adaptor is not None and round_index < supervised_config.max_rounds:
            try:
                adapted = str(continue_adaptor(state.rounds) or "").strip()
                if adapted:
                    prior = str(state.reviewer_next_action or "").strip()
                    state.reviewer_next_action = (
                        "## Scientist alternative playbook for the next round\n"
                        + adapted
                        + ("\n\n## Reviewer guidance\n" + prior if prior else "")
                    )
            except Exception:  # noqa: BLE001 — adaptation is advisory
                log.debug("continue adaptor failed", exc_info=True)
        return control_proceed()
