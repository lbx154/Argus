"""Round-loop progress bookkeeping and optional low-risk self-review."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import ReviewDecision
from ..core.role_handoff import (
    EngineerHandoff,
    decision_engineer_handoff,
    parse_engineer_handoff,
)
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
)
from .round_stop_signals import _runner_result_has_successful_work_signal

if TYPE_CHECKING:
    from .runner import SupervisedConfig


def _control_line(line: str) -> str:
    text = str(line or "").strip()
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        text = text[1:-1].strip()
    return text


def _round_handoff(outcome: EngineerTurnOutcome) -> EngineerHandoff:
    """Who owns the work next, read from the decision the Engineer recorded.

    Falling back to the round message is for a turn that recorded no decision
    at all. When one exists it is the answer, so a sentence in the narrative
    cannot nominate a different owner than the Engineer chose.
    """
    if isinstance(outcome.decision, dict):
        return decision_engineer_handoff(outcome.decision)
    return parse_engineer_handoff(outcome.engineer_message)


def _milestone_is_done(outcome: EngineerTurnOutcome) -> bool:
    if isinstance(outcome.decision, dict):
        return str(outcome.decision.get("status") or "").strip().lower() == "done"
    from ..core.role_reply import decision_footer_text

    return any(
        _control_line(line).casefold() == "milestone_status=done"
        for line in decision_footer_text(outcome.engineer_message).splitlines()
    )


def _milestone_is_blocked(outcome: EngineerTurnOutcome) -> bool:
    if isinstance(outcome.decision, dict):
        return str(outcome.decision.get("status") or "").strip().lower() == "blocked"
    from ..core.role_reply import decision_footer_text

    return any(
        _control_line(line).casefold() == "milestone_status=blocked"
        for line in decision_footer_text(outcome.engineer_message).splitlines()
    )


class RoundSelfReviewMixin:
    """Update progress state and settle low-risk work without another model."""

    def _handle_progress_and_self_review(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        review_completed_hook,
        continue_adaptor,
        on_event: Callable[[dict], None] | None,
    ) -> RoundControl:
        state.backend_failure_streak = 0
        successful_work = _runner_result_has_successful_work_signal(
            outcome.engineer_result,
            engineer_message=outcome.engineer_message,
        )
        if successful_work:
            state.no_progress_streak = 0
        else:
            state.no_progress_streak += 1
        milestone_done = _milestone_is_done(outcome)
        handoff = _round_handoff(outcome)
        if handoff.waits_for_operator:
            return self._settle_round(
                review=ReviewDecision(
                    status="blocked",
                    reason="Engineer requires an operator-owned decision before continuing.",
                    next_action="Resume after the operator answers the pending question.",
                    operator_question=handoff.operator_question,
                    operator_options=list(handoff.operator_options),
                    review_source="engineer_operator_question",
                    planner_report={
                        "plan_signal": "continue",
                        "challenge": handoff.operator_question,
                        "authority_impact": "operator",
                    },
                ),
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                continue_adaptor=continue_adaptor,
                on_event=on_event,
            )
        if (
            not supervised_config.require_independent_review
            and _milestone_is_blocked(outcome)
        ):
            result = (
                str(outcome.decision.get("result") or "").strip()
                if isinstance(outcome.decision, dict)
                else ""
            )
            return self._settle_round(
                review=ReviewDecision(
                    status="blocked",
                    reason=result or "Engineer reported an unresolved blocker.",
                    next_action="",
                    review_source="engineer_self_review",
                ),
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                continue_adaptor=continue_adaptor,
                on_event=on_event,
            )
        if handoff.next_owner == "engineer":
            return control_continue_loop()
        if not supervised_config.require_independent_review and successful_work:
            if milestone_done:
                return self._settle_round(
                    review=ReviewDecision(
                        status="done",
                        reason=(
                            "Engineer reported the requested milestone complete; "
                            "independent review was not required for this mission."
                        ),
                        next_action="",
                        review_source="engineer_self_review",
                    ),
                    round_index=round_index,
                    supervised_config=supervised_config,
                    workdir=workdir,
                    outcome=outcome,
                    state=state,
                    review_completed_hook=review_completed_hook,
                    continue_adaptor=continue_adaptor,
                    on_event=on_event,
                )
            return control_continue_loop()
        return control_proceed()


__all__ = ["RoundSelfReviewMixin"]
