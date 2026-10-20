"""Route a turn by its baton line: the holder it names is where the work goes.

The host enforces one invariant on the way (docs/role-handoff-design.md, "The
baton", invariant 3): work that would return to a holder that already held it
as it is now, with nothing new from outside since, goes one rung up the ladder
instead of calling that model again.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.models import ReviewDecision, RunnerResult
from ..core.role_handoff import (
    HANDOFF_STATUS,
    LADDER,
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


def outside_inputs(state: RoundLoopState) -> list[object] | None:
    """What reached the work from outside the team.

    When the operator last wrote, the operator's ledger revision and the
    Manager's direction revision; ``None`` when any cannot be read, since
    unknown inputs never count as unchanged.
    """
    from ..core.operator_context import OperatorContextStore
    from ..core.operator_presence import operator_presence
    from .round_manager_wait import manager_direction_revision

    root = state.state_root
    if root is None:
        return None
    try:
        return [
            operator_presence(root).last_spoke_at,
            OperatorContextStore(root).revision,
            manager_direction_revision(root),
        ]
    except Exception:  # noqa: BLE001 - an unreadable ledger is not "nothing new"
        return None


def returned_unchanged(holder: str, state: RoundLoopState, label: str) -> bool:
    """Whether ``holder`` already held this work as snapshot ``label`` shows it, with nothing new since."""
    return state.snapshots is not None and state.snapshots.held_unchanged(holder, label, outside_inputs(state))


def no_turn(state: RoundLoopState) -> EngineerTurnOutcome:
    """The round's Engineer slot when the work climbed past the Engineer: no turn ran."""
    return EngineerTurnOutcome(
        engineer_result=RunnerResult(exit_code=0), round_thread_id=None, fatal_error=None,
        safe_fatal_error=None, stop_kind=None, raw_engineer_message="", engineer_message="",
        process_ownership_note="", round_started_at=time.time(),
    )


def climb_record(passed: list[str], holder: str, *, handed: ReviewDecision | None = None) -> ReviewDecision:
    """The host's record that unchanged work climbed past ``passed`` to ``holder``.

    When the Reviewer's verdict ``handed`` it on, the round keeps that one
    record: the verdict's findings stay, its own words in ``reviewer_words``,
    and where the work went is the host's.
    """
    who = " and the ".join(passed)
    reason = (
        f"Nothing changed since the {who} last held this work, and nothing new "
        f"arrived from outside, so it went one rung up, to the {holder}."
    )
    if handed is not None:
        reason += f" The Reviewer had handed it to the {passed[0]}: {handed.reason}"
    return replace(
        handed or ReviewDecision(status="continue", reason="", next_action=""),
        status=HANDOFF_STATUS[holder],
        reason=reason,
        next_action="",
        operator_question=(
            f"Nothing changed since the {who} last held this work. How should it go on?"
            if holder == "operator" else ""
        ),
        review_source="unchanged_return",
        host_placeholder="unchanged_return",
        reviewer_authored=False,
        reviewer_words=None if handed is None else handed.reviewer_words or {
            "status": handed.status, "reason": handed.reason, "next_action": handed.next_action,
        },
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
    """Hand a turn to the holder its baton line names."""

    def _deliver(self, holder: str, *, state: RoundLoopState, label: str) -> tuple[str, list[str]]:
        """Where work handed to ``holder`` goes, and the holders it climbed past.

        Up to the operator, each holder that already held the work as snapshot
        ``label`` shows it, with nothing new since, passes it one rung up. The
        Planner and the Manager hold it from here; the Engineer and the
        Reviewer record their own hold when their turn completes.
        """
        passed: list[str] = []
        while holder in LADDER[:-1] and returned_unchanged(holder, state, label):
            passed.append(holder)
            holder = LADDER[LADDER.index(holder) + 1]
        if holder in {"planner", "manager"} and state.snapshots is not None:
            state.snapshots.hold(holder, label, outside_inputs(state))
        return holder, passed

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
        climbed: bool = False,
    ) -> RoundControl:
        """Route the turn by its line; ``climbed``: no turn ran, the work climbed past the Engineer."""
        state.backend_failure_streak = 0
        # A turn that reached this phase was not a backend failure, so it ends
        # any run of identical failures: the same-cause count restarts from
        # one if that signature ever returns. Without this reset, scattered
        # rate-limit failures across a long task would read as one continuing
        # outage and open the hold on an isolated accident.
        state.backend_failure_signature = ""
        state.backend_failure_same_cause_streak = 0
        _record_stated_assumption(outcome, supervised_config)
        if state.snapshots is not None:
            # The Engineer held the work as its turn began.
            state.snapshots.hold("engineer", "turn", state.handed_inputs)
        wanted = "reviewer" if climbed else _round_handoff(outcome).next_owner
        if wanted == "done" and supervised_config.require_independent_review:
            # Accepting a candidate is the Reviewer's where review is required.
            wanted = "reviewer"
        holder, passed = (
            self._deliver(wanted, state=state, label="now")
            if state.snapshots is not None and state.snapshots.take("now") else (wanted, [])
        )
        passed = ["engineer", *passed] if climbed else passed
        if holder == "engineer":
            return control_continue_loop()
        if holder == "reviewer":
            return control_proceed()
        return self._settle_round(
            review=climb_record(passed, holder) if passed else _handoff_record(holder, outcome),
            round_index=round_index,
            supervised_config=supervised_config,
            workdir=workdir,
            outcome=outcome,
            state=state,
            review_completed_hook=review_completed_hook,
            on_event=on_event,
        )


__all__ = ["RoundSelfReviewMixin"]
