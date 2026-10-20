"""Settle a round from the Reviewer's minimal verdict."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import LoopStatus, ReviewDecision, RoundRecord
from ..core.venue_review import ACCEPTANCE_BAR_POLICY
from .checkpoint import resolve_shared_checkpoint
from .round_config import (
    HOST_PLACEHOLDER_REVIEW_SOURCES,
    OPERATOR_QUESTION_FORBIDDEN_NEXT_ACTION,
    QUESTION_POLICY,
)
from .round_signals import _review_event_payload
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
    control_return,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig

log = logging.getLogger(__name__)


def _operator_questions_allowed(supervised_config: "SupervisedConfig") -> bool:
    from ..core.autonomy import operator_questions_can_be_asked

    root = getattr(supervised_config, "operator_question_policy_root", None)
    if root is not None:
        return operator_questions_can_be_asked(root)
    return (
        bool(getattr(supervised_config, "operator_questions_allowed", True))
        and operator_questions_can_be_asked()
    )


def _operator_only_action(review: ReviewDecision) -> bool:
    """Whether the question asks for an action only the operator could approve.

    Such a question is never settled on an assumption: it stays a question, and
    while the operator cannot be asked the Manager decides it in their place
    once the mission parks on it.
    """
    from ..core.autonomy import OPERATOR_ACTION_NEEDS, normalize_operator_need

    report = review.planner_report if isinstance(review.planner_report, dict) else {}
    return normalize_operator_need(report.get("operator_need")) in OPERATOR_ACTION_NEEDS


def _record_forbidden_question(
    review: ReviewDecision,
    supervised_config: "SupervisedConfig",
) -> None:
    """Keep the record the run report is built from.

    The team now settles the question itself; it is logged as a conflict whose
    chosen reading the Engineer states (``ASSUMPTION=``) and CHECKPOINT.md keeps.
    """
    from ..core.autonomy import record_autonomous_assumption

    root = getattr(supervised_config, "operator_question_policy_root", None)
    if root is None:
        return
    record_autonomous_assumption(
        root,
        item_id="",
        conflict=review.operator_question,
        source="round_question",
    )


def _question_withheld(review: ReviewDecision, supervised_config: "SupervisedConfig") -> bool:
    """Whether this verdict's question may not go to the operator."""
    return bool(
        review.operator_question
        and not review.intervention(QUESTION_POLICY)
        and not _operator_questions_allowed(supervised_config)
        and not _operator_only_action(review)
    )


def _withheld(review: ReviewDecision, action: str, outcome: str) -> ReviewDecision:
    """The verdict as written, with the question-policy intervention beside it."""
    return replace(review, interventions=[*review.interventions, {
        "policy": QUESTION_POLICY,
        "action": action,
        "at": time.time(),
        "note": "the operator may not be asked in this mission, so the question was not sent; "
        + outcome,
    }])


def _apply_operator_question_policy(
    review: ReviewDecision,
    *,
    supervised_config: "SupervisedConfig",
    state: RoundLoopState,
) -> ReviewDecision:
    """Record beside the verdict that its question was not sent to the operator.

    The verdict stays as its author wrote it. The intervention names the
    policy, what the host did instead of asking, and when: the team first
    carries the obstacle on; when that did not clear it, the mission ends and
    the Manager decides in the operator's place.
    """
    if not _question_withheld(review, supervised_config):
        return review
    # A turn nobody judged (a turn-limit restart, a dropped session) between
    # two forbidden questions does not make the second one new.
    judged = [
        record for record in state.rounds
        if record.review.review_source not in HOST_PLACEHOLDER_REVIEW_SOURCES
    ]
    _record_forbidden_question(review, supervised_config)
    if judged and judged[-1].review.intervention(QUESTION_POLICY):
        return _withheld(
            review, "manager",
            "carrying the obstacle on did not clear it, so the Manager decides in the "
            "operator's place",
        )
    return _withheld(
        review, "continue",
        "the team carries the obstacle on and settles it on the most defensible reading",
    )


def withhold_terminal_question(terminal: tuple, supervised_config: "SupervisedConfig") -> tuple:
    """Record beside a final record whose question may not go out that it was not sent.

    Host stop records (an expired login, say) reach the end of a run without
    settlement; the outcome reads this intervention and sends no question.
    """
    rounds = terminal[1]
    if rounds and _question_withheld(rounds[-1].review, supervised_config):
        rounds[-1] = replace(
            rounds[-1],
            review=_withheld(rounds[-1].review, "not_sent", "the run ends without it"),
        )
    return terminal


def _next_engineer_action(review: ReviewDecision) -> str | None:
    """What the next Engineer turn works from: the verdict, and an intervention that kept the work going."""
    if review.intervention(QUESTION_POLICY):
        parts: tuple[str, ...] = (review.next_action, OPERATOR_QUESTION_FORBIDDEN_NEXT_ACTION)
    elif review.status == "continue":
        return review.next_action
    elif review.intervention(ACCEPTANCE_BAR_POLICY):
        parts = (review.reason, review.next_action, *(row["note"] for row in review.interventions))
    else:
        return None
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
        if review.status == "blocked":
            return "blocked", review.reason or "Reviewer blocked progress."
        if review.status == "replan_requested":
            return (
                "replan_requested",
                review.reason or "Reviewer requested a Manager-owned replacement plan.",
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
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        review = _apply_operator_question_policy(
            review,
            supervised_config=supervised_config,
            state=state,
        )
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
        withheld = review.intervention(QUESTION_POLICY)
        if withheld is not None:
            terminal_status, reason = (
                (
                    "blocked",
                    "Operator questions are forbidden and carrying the obstacle on did "
                    "not clear it; the Manager decides in the operator's place: "
                    + review.operator_question
                    + ("\n\nThe review's own findings:\n" + review.reason if review.reason else ""),
                )
                if withheld["action"] == "manager"
                else (None, "")
            )
        from .round_manager_wait import manager_wait_terminal

        paused = manager_wait_terminal(supervised_config, state)
        if paused is not None:
            return control_return(paused)
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
        if withheld is not None:
            return control_continue_loop()
        return control_proceed()
