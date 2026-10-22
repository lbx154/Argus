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
from ..core.operator_decision import normalize_agent_options
from ..core.role_handoff import HANDOFF_STATUS, LADDER, Handoff, read_baton_line, strip_handoff
from .round_reviewer import backlog_history
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig


def _handoff_record(holder: str, handoff: Handoff | None, message: str) -> ReviewDecision:
    """The round's record of the Engineer handing the work past the Reviewer.

    The Engineer's own words, under its own source: the host writes no verdict
    for it. ``done`` is the Engineer accepting where no review is required.
    """
    return ReviewDecision(
        status=HANDOFF_STATUS[holder],
        reason=strip_handoff(message) or f"Engineer handed the work to the {holder}.",
        next_action="",
        operator_question=handoff.question if handoff else "",
        operator_options=normalize_agent_options(handoff.options if handoff else ()),
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


def accepted_as_is(state: RoundLoopState, label: str) -> bool:
    """Whether the Reviewer accepted the work as snapshot ``label`` shows it, with nothing new since.

    The host refusing that acceptance is new: once the task it ended is
    anything but done in the backlog (failed, or sent back to run again), the
    acceptance no longer stands, and the unchanged work climbs past the
    Reviewer like any unchanged return. An unreadable backlog keeps nothing.
    """
    verdict = state.snapshots.held_verdict("reviewer") if state.snapshots is not None else None
    if not verdict or not returned_unchanged("reviewer", state, label):
        return False
    rows = backlog_history(state.state_root)
    return rows is not None and not any(
        row.id == verdict["mission"] and row.status != "done" for row in rows
    )


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


#: What an accepting verdict decided, kept beside the Reviewer's hold so an
#: unchanged rerun can stand on it.
_VERDICT_FIELDS = (
    "reason", "residual_risk", "residual_risk_detail", "research_result", "venue_review",
    "venue_review_required", "venue_review_passed", "venue_review_snapshot", "manuscript_snapshot",
)


def accepted_verdict(review: ReviewDecision, round_index: int, state: RoundLoopState) -> dict | None:
    """The Reviewer's acceptance, kept with its hold, or ``None`` when the verdict accepted nothing."""
    if review.status != "done" or not review.final_submission_certified:
        return None
    return {
        **{name: getattr(review, name) for name in _VERDICT_FIELDS},
        "round": round_index, "mission": state.mission_item_id or state.mission_ref, "at": time.time(),
    }


def reused_record(verdict: dict) -> ReviewDecision:
    """The Reviewer's acceptance of this very candidate, standing again without a model call."""
    which = f"round {verdict['round']}" + (f" of {verdict['mission']}" if verdict["mission"] else "")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(verdict["at"]))
    return ReviewDecision(
        status="done",
        next_action="",
        **{name: verdict[name] for name in _VERDICT_FIELDS},
        interventions=[{
            "policy": "unchanged_return",
            "action": "reused_verdict",
            "at": time.time(),
            "note": (
                f"the candidate is unchanged since the Reviewer accepted it in {which} ({when}) "
                "and nothing new arrived, so that acceptance stands; no model was called"
            ),
        }],
    )


class RoundRoutingMixin:
    """Hand a turn to the holder its baton line names."""

    def _deliver(self, holder: str, *, state: RoundLoopState, label: str) -> tuple[str, list[str]]:
        """Where work handed to ``holder`` goes, and the holders it climbed past.

        Up to the operator, each holder that already held the work as snapshot
        ``label`` shows it, with nothing new since, passes it one rung up;
        a Reviewer that accepted it as it is sends it to ``done`` instead. The
        Planner and the Manager hold it from here; the Engineer and the
        Reviewer record their own hold when their turn completes.
        """
        passed: list[str] = []
        while holder in LADDER[:-1] and returned_unchanged(holder, state, label):
            if holder == "reviewer" and accepted_as_is(state, label):
                return "done", passed
            passed.append(holder)
            holder = LADDER[LADDER.index(holder) + 1]
        if holder in {"planner", "manager"} and state.snapshots is not None:
            state.snapshots.hold(holder, label, outside_inputs(state))
        return holder, passed

    def _route_turn(
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
        if not climbed:
            state.review_unseen = False
        if state.snapshots is not None:
            # The Engineer held the work as its turn began.
            state.snapshots.hold("engineer", "turn", state.handed_inputs)
        # A missing or unreadable line names the Reviewer: never done.
        handoff = None if climbed else read_baton_line(outcome.engineer_message)
        wanted = handoff.holder if handoff else "reviewer"
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
        # Sent to ``done`` past the Reviewer: its acceptance of this candidate stands.
        verdict = (
            state.snapshots.held_verdict("reviewer")
            if holder == "done" and wanted != "done" and state.snapshots else None
        )
        if verdict:
            review = reused_record(verdict)
        elif passed:
            review = climb_record(passed, holder)
        else:
            review = _handoff_record(holder, handoff, outcome.engineer_message)
        return self._settle_round(
            review=review,
            round_index=round_index,
            supervised_config=supervised_config,
            workdir=workdir,
            outcome=outcome,
            state=state,
            review_completed_hook=review_completed_hook,
            on_event=on_event,
        )


__all__ = ["RoundRoutingMixin"]
