"""Route an Engineer turn by its baton line: the holder it names is where the work goes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import ReviewDecision
from ..core.role_handoff import (
    HANDOFF_STATUS,
    EngineerHandoff,
    parse_engineer_handoff,
    strip_handoff,
)
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig


def _round_handoff(outcome: EngineerTurnOutcome) -> EngineerHandoff:
    """The handoff the Engineer's turn ends with.

    A recorded decision is read as the line it renders, the one the Reviewer
    and the journal see, so a sentence in the narrative cannot name another
    holder. A missing or unreadable line names the Reviewer: never done.
    """
    if isinstance(outcome.decision, dict):
        from .round_execution import _engineer_decision_message

        return parse_engineer_handoff(_engineer_decision_message(outcome.decision))
    return parse_engineer_handoff(outcome.engineer_message)


def _handoff_record(holder: str, outcome: EngineerTurnOutcome) -> ReviewDecision:
    """The round's record of the Engineer handing the work past the Reviewer.

    The Engineer's own words, under its own source: the host writes no verdict
    for it. ``done`` is the Engineer accepting where no review is required.
    """
    handoff = _round_handoff(outcome)
    why = strip_handoff(outcome.engineer_message)[-1200:]
    return ReviewDecision(
        status=HANDOFF_STATUS[holder],
        reason=why or f"Engineer handed the work to the {holder}.",
        next_action="",
        operator_question=handoff.operator_question,
        operator_options=list(handoff.operator_options),
        review_source="engineer_self_review" if holder == "done" else "engineer_handoff",
    )


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
    """Hand an Engineer turn to the holder its baton line names."""

    def _handle_progress_and_self_review(
        self,
        *,
        round_index: int,
        supervised_config: "SupervisedConfig",
        workdir: Path,
        outcome: EngineerTurnOutcome,
        state: RoundLoopState,
        review_completed_hook,
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
        _record_stated_assumption(outcome, supervised_config)
        holder = _round_handoff(outcome).next_owner
        if holder == "done" and supervised_config.require_independent_review:
            # Accepting a candidate is the Reviewer's where review is required.
            holder = "reviewer"
        if holder == "engineer":
            return control_continue_loop()
        if holder == "reviewer":
            return control_proceed()
        return self._settle_round(
            review=_handoff_record(holder, outcome),
            round_index=round_index,
            supervised_config=supervised_config,
            workdir=workdir,
            outcome=outcome,
            state=state,
            review_completed_hook=review_completed_hook,
            on_event=on_event,
        )


__all__ = ["RoundSelfReviewMixin"]
