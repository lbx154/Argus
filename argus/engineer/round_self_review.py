"""Round-loop progress bookkeeping and optional low-risk self-review."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import ReviewDecision
from ..core.role_handoff import (
    EngineerHandoff,
    decision_engineer_handoff,
    parse_engineer_handoff,
    read_baton_line,
    strip_handoff,
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

# Consecutive unjudged Engineer turns (NEXT_OWNER=engineer, or an unfinished
# self-reviewed milestone) allowed before the Reviewer judges.
ENGINEER_SELF_HANDOFF_LIMIT = 2


def _unjudged_turns_capped(supervised_config: "SupervisedConfig") -> bool:
    """Whether consecutive unjudged Engineer turns stop at the limit.

    Where independent review is required they always do. Where it is not, a
    positive ``max_rounds`` already ends the loop, so only an uncapped run
    needs the count: its only other exit is a ``done`` the Engineer might
    never write. That case is a stopgap until HO-12 hands an unchanged return
    upward on what changed rather than on a count.
    """
    return (
        supervised_config.require_independent_review
        or supervised_config.max_rounds <= 0
    )


def _round_handoff(outcome: EngineerTurnOutcome) -> EngineerHandoff:
    """Who owns the work next, read from the decision the Engineer recorded.

    Falling back to the round message is for a turn that recorded no decision
    at all. When one exists it is the answer, so a sentence in the narrative
    cannot nominate a different owner than the Engineer chose.
    """
    if isinstance(outcome.decision, dict):
        return decision_engineer_handoff(outcome.decision)
    return parse_engineer_handoff(outcome.engineer_message)


def _milestone_status(outcome: EngineerTurnOutcome) -> str:
    """The milestone status the Engineer stated: its decision, its baton line, else its footer.

    A baton line says ``done`` only as ``→ done``; any other line, or one that
    could not be read, leaves the milestone open. The footer is read with the
    reader every other role decision uses, so ``MILESTONE_STATUS: done``, a
    bullet, bold or backticks all say ``done``.
    """
    if isinstance(outcome.decision, dict):
        return str(outcome.decision.get("status") or "").strip().lower()
    baton = read_baton_line(outcome.engineer_message)
    if baton is not None:
        return "done" if baton.next_owner == "done" else ""
    from ..core.role_reply import decision_footer_text, read_key_values

    found = read_key_values(
        decision_footer_text(outcome.engineer_message), ("MILESTONE_STATUS",)
    )
    return str(found.get("MILESTONE_STATUS") or "").strip("`*_. ").casefold()


def _milestone_is_done(outcome: EngineerTurnOutcome) -> bool:
    return _milestone_status(outcome) == "done"


def _milestone_is_blocked(outcome: EngineerTurnOutcome) -> bool:
    return _milestone_status(outcome) == "blocked"


def _footer_block(footer: str, name: str, *, limit: int = 2400) -> str:
    """A ``NAME=`` footer value with its continuation lines kept whole.

    The value runs until the next ``KEY=`` control line, so a reading the
    Engineer wrote over several lines is recorded as written.
    """
    collected: list[str] = []
    capturing = False
    for raw_line in str(footer or "").splitlines():
        line = raw_line.strip().strip("`")
        key, separator, value = line.partition("=")
        is_control = bool(separator) and key.strip().isupper() and " " not in key.strip()
        if is_control:
            if capturing:
                break
            if key.strip() == name:
                capturing = True
                collected.append(value.strip())
            continue
        if capturing:
            collected.append(raw_line.rstrip())
    return "\n".join(part for part in collected).strip()[:limit]


def _record_stated_assumption(
    outcome: EngineerTurnOutcome, supervised_config: "SupervisedConfig"
) -> None:
    """Keep the reading the Engineer chose when no operator could be asked.

    The run report lists the reading in the Engineer's own words, from its
    decision (``assumption``) or footer (``ASSUMPTION=``).
    """
    from ..core.autonomy import operator_questions_can_be_asked, record_autonomous_assumption
    from ..core.role_reply import decision_footer_text

    root = getattr(supervised_config, "operator_question_policy_root", None)
    if root is None or operator_questions_can_be_asked(root):
        return
    reading = ""
    if isinstance(outcome.decision, dict):
        reading = str(outcome.decision.get("assumption") or "").strip()
    if not reading:
        reading = _footer_block(decision_footer_text(outcome.engineer_message), "ASSUMPTION")
    if reading:
        record_autonomous_assumption(
            root, item_id="", reading=reading, source="engineer"
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
        # A turn that reached this phase was not a backend failure, so it ends
        # any run of identical failures: the same-cause count restarts from
        # one if that signature ever returns. Without this reset, scattered
        # rate-limit failures across a long task would read as one continuing
        # outage and open the hold on an isolated accident.
        state.backend_failure_signature = ""
        state.backend_failure_same_cause_streak = 0
        successful_work = _runner_result_has_successful_work_signal(
            outcome.engineer_result,
            engineer_message=outcome.engineer_message,
        )
        if successful_work:
            state.no_progress_streak = 0
        else:
            state.no_progress_streak += 1
        milestone_done = _milestone_is_done(outcome)
        _record_stated_assumption(outcome, supervised_config)
        handoff = _round_handoff(outcome)
        if handoff.waits_for_operator:
            from ..core.autonomy import assess_operator_intervention
            from .round_settlement import _operator_questions_allowed

            # The Engineer classified its own question; words in the question
            # or the round message are not consulted. An explicit
            # NEXT_OWNER=operator it did not classify fails safe and goes to
            # the operator, when one may be asked. A question without that
            # explicit handoff (or a run where questions cannot be asked) is
            # a technical fact for the Reviewer.
            intervention = assess_operator_intervention(
                question=handoff.operator_question,
                operator_need=handoff.operator_need,
                unclassified_requires_operator=(
                    handoff.source == "structured"
                    and _operator_questions_allowed(supervised_config)
                ),
            )
            if not intervention.required:
                # The Reviewer sees the Engineer's question in the ordinary
                # round record and can return it to Planner as a technical fact.
                return control_proceed()
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
                        "operator_need": intervention.operator_need,
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
        if handoff.next_owner in {"planner", "manager"}:
            # Routing is the Engineer's: a mis-scoped task goes to the Planner,
            # an obstacle only the Manager can remove goes to the Manager.
            # Neither accepts work, so no review stands in between.
            why = strip_handoff(outcome.engineer_message)[-1200:]
            to_planner = handoff.next_owner == "planner"
            return self._settle_round(
                review=ReviewDecision(
                    status="replan_requested" if to_planner else "blocked",
                    reason=why or f"Engineer handed the work to the {handoff.next_owner}.",
                    next_action="",
                    review_source="engineer_handoff",
                    planner_report=(
                        {} if to_planner
                        else {"manager_attention": "needed", "manager_attention_reason": why[:200]}
                    ),
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
                    reason=result or "Engineer reported an obstacle it could not clear.",
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
        if handoff.next_owner == "engineer" and not state.pending_external_wait_review:
            # A self-handoff skips the Reviewer and, with it, every round guard
            # (those are evaluated at settlement). Where the run is capped (see
            # _unjudged_turns_capped), two in a row continue and the third goes
            # to the Reviewer; a turn after rounds that produced nothing goes
            # on too, so the Engineer cannot run unjudged or spin on itself.
            stalled = (
                supervised_config.no_progress_threshold > 0
                and state.no_progress_streak >= supervised_config.no_progress_threshold
            )
            if not stalled and (
                not _unjudged_turns_capped(supervised_config)
                or state.engineer_self_handoffs < ENGINEER_SELF_HANDOFF_LIMIT
            ):
                state.engineer_self_handoffs += 1
                return control_continue_loop()
            if supervised_config.require_independent_review:
                return control_proceed()
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
            # An unfinished self-reviewed milestone is the same unjudged
            # continuation as a self-handoff and shares its cap; past it the
            # Reviewer judges the turn and settlement's round guards apply.
            if (
                not _unjudged_turns_capped(supervised_config)
                or state.engineer_self_handoffs < ENGINEER_SELF_HANDOFF_LIMIT
            ):
                state.engineer_self_handoffs += 1
                return control_continue_loop()
        return control_proceed()


__all__ = ["RoundSelfReviewMixin"]
