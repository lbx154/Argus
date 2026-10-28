"""Settle a round on the handoff that ends it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import LoopStatus, ReviewDecision, RoundRecord
from ..core.venue_review import ACCEPTANCE_BAR_POLICY
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


def _next_engineer_action(review: ReviewDecision) -> str | None:
    """What the next Engineer turn works from: the verdict, and an intervention that kept the work going."""
    if review.status == "engineer":
        return review.next_action
    if not review.intervention(ACCEPTANCE_BAR_POLICY):
        return None
    parts = (review.reason, review.next_action, *(row["note"] for row in review.interventions))
    return "\n\n".join(part.strip() for part in parts if str(part or "").strip())


def _record_operator_acceptance(state: RoundLoopState, review: ReviewDecision) -> None:
    """Keep the operator's acceptance of a residual risk the verdict carries.

    The operator's choice on the decision card, which the host found before
    recording the verdict, is kept like the Manager's: scoped to this item or,
    with none, to this mission.
    """
    detail = review.residual_risk_detail if review.status == "done" else {}
    if (
        state.state_root is None
        or (review.review_source or "reviewer") != "reviewer"
        or detail.get("accepted_by") != "operator"
    ):
        return
    from ..core.residual_risk import accept_residual_risk

    try:
        accept_residual_risk(
            state.state_root, check=detail.get("check", ""), risk=detail.get("risk", ""),
            accepted_by="operator", item_id=state.mission_item_id, mission_ref=state.mission_ref,
            basis=detail.get("basis", ""), basis_source=detail.get("basis_source", ""),
            source_ref=f"operator:{detail.get('acceptance', '')}",
        )
    except OSError:
        log.warning("operator residual-risk acceptance could not be recorded", exc_info=True)


class RoundSettlementMixin:
    """Mixin providing ``SupervisedEngineer``'s round-settlement phase and
    the ``_classify`` terminal-status decision gate."""

    @staticmethod
    def _classify(*, review: ReviewDecision) -> tuple[LoopStatus | None, str]:
        """The terminal status a verdict settles, or ``None`` to go on."""
        if review.status == "done":
            if not review.final_submission_certified:
                # The operator's acceptance bar holds the paper open; the
                # host's note beside the verdict says why.
                return None, ""
            return "done", review.reason or "Reviewer judged the objective complete."
        if review.status in {"planner", "manager", "operator"}:
            return review.status, review.reason or f"Reviewer handed the work to the {review.status}."
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
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        engineer_result = outcome.engineer_result
        engineer_message = outcome.engineer_message
        state.reviewer_backend_failure_streak = 0
        state.pending_secret_guard_notes.clear()
        _record_operator_acceptance(state, review)
        if on_event:
            on_event(_review_event_payload(
                review,
                round_index=round_index,
                round_max=supervised_config.max_rounds,
                text=f"review: {review.status} — {review.reason}",
            ))
        record = RoundRecord(
            round_index=round_index,
            engineer_message=engineer_message,
            engineer_exit_code=engineer_result.exit_code,
            review=review,
            fatal_error=engineer_result.fatal_error,
        )
        state.rounds.append(record)
        state.reviewer_next_action = _next_engineer_action(review)
        # Seal the Reviewer half of the round packet, symmetrically with
        # ``record_engineer_handoff`` in round_execution. Sealed before the
        # terminal classification below, because the terminal round is exactly
        # the one whose verdict the campaign-level stage reconciliation needs.
        #
        # Only a genuine independent Reviewer verdict is sealed: the packet
        # declares ``producer_role="reviewer"`` and the supervisor replays it
        # as independent stage evidence, so sealing a self-review here would
        # let the Engineer certify its own stage transition.
        if supervised_config.context_packet_path and (
            review.review_source or "reviewer"
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

        terminal_status, reason = self._classify(review=review)
        # A verdict is a completed role operation: a Manager STOP applies at
        # the loop's next boundary, never over a judgment already made.
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
        return control_proceed()
