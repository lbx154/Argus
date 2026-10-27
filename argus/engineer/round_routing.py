"""Route a turn by its hand_off: the holder it names is where the work goes.

The host enforces one invariant on the way (docs/role-handoff-design.md, "The
baton", invariant 3): when no turn ran, work that would return to a holder that
already held it as it is now, with nothing new from outside since, goes one
rung up the ladder instead of calling that model again.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.handoff import LADDER, HandOff, HandOffActions
from ..core.models import ReviewDecision, RunnerResult
from .round_reviewer import backlog_history, mission_questions_allowed
from .round_state import (
    EngineerTurnOutcome,
    RoundControl,
    RoundLoopState,
    control_continue_loop,
    control_proceed,
)

if TYPE_CHECKING:
    from .runner import SupervisedConfig


def _handoff_record(holder: str, handoff: HandOff) -> ReviewDecision:
    """The round's record of the Engineer handing the work past the Reviewer.

    The Engineer's own words, under its own source: the host writes no verdict
    for it. ``done`` is the Engineer accepting where no review is required.
    """
    return ReviewDecision(
        status=holder,
        reason=handoff.summary,
        next_action=handoff.note,
        operator_question=handoff.question,
        operator_options=handoff.options,
        review_source="engineer_self_review" if holder == "done" else "engineer_handoff",
    )


def engineer_actions(supervised_config: "SupervisedConfig") -> HandOffActions:
    """The Engineer's form for one turn: it accepts its own work only where no review is required."""

    def check(payload: dict) -> None:
        if payload["to"] == "done" and supervised_config.require_independent_review:
            raise ValueError("This task requires an independent review: hand it to=reviewer.")

    return HandOffActions(
        "engineer", check=check, operator_available=mission_questions_allowed(supervised_config),
    )


def sent_to(handoff: HandOff | None) -> str:
    """Where a turn sends the work: the holder its hand_off names; without one, the Reviewer."""
    return handoff.to if handoff else "reviewer"


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
    """Whether ``holder`` already held this work as snapshot ``label`` shows it, with nothing new since.

    Review once: a closing step (a final acceptance, or the task that closes
    a stage) judges files other tasks made, so past the Engineer a holder that
    held the very same files on another task held this work too. The closing
    step's Engineer still gets its turn, and other tasks keep their own line.
    """
    if state.snapshots is None:
        return False
    inputs = outside_inputs(state)
    return state.snapshots.held_unchanged(holder, label, inputs) or (
        state.closing and holder != "engineer" and bool(state.snapshots.held_elsewhere(holder, label, inputs))
    )


def accepted_as_is(state: RoundLoopState, label: str) -> dict | None:
    """The Reviewer's acceptance of the work as snapshot ``label`` shows it, with nothing new since.

    For a closing step, an acceptance of the same files on another task
    stands too. The host refusing that acceptance is new: once the task it
    ended is anything but done in the backlog (failed, or sent back to run
    again), the acceptance no longer stands, and the unchanged work climbs
    past the Reviewer like any unchanged return. An unreadable backlog keeps
    nothing.
    """
    snapshots = state.snapshots
    if snapshots is None:
        return None
    inputs = outside_inputs(state)
    if snapshots.held_unchanged("reviewer", label, inputs):
        verdicts = [snapshots.held_verdict("reviewer")]
    else:
        held = snapshots.held_elsewhere("reviewer", label, inputs) if state.closing else []
        verdicts = sorted((row.get("verdict") for row in held), key=lambda verdict: (verdict or {}).get("at") or 0)
    verdict = verdicts[-1] if verdicts else None
    if not isinstance(verdict, dict):
        return None
    rows = backlog_history(state.state_root)
    if rows is None or any(row.id == verdict.get("mission") and row.status != "done" for row in rows):
        return None
    return verdict


def no_turn(state: RoundLoopState) -> EngineerTurnOutcome:
    """The round's Engineer slot when the work climbed past the Engineer: no turn ran."""
    return EngineerTurnOutcome(
        engineer_result=RunnerResult(exit_code=0), round_thread_id=None, fatal_error=None,
        safe_fatal_error=None, stop_kind=None, handoff=None, engineer_message="",
        process_ownership_note="", round_started_at=time.time(),
    )


def climb_record(holder: str, *, chinese: bool) -> ReviewDecision:
    """The host's record that unchanged work climbed to ``holder``.

    Said once, in the task's language, as what it means for the work: the
    person reads it as written, so it names no role. ``review_source`` says
    it climbed.
    """
    if holder == "operator":
        reason = ("这条路没有新进展，需要你决定怎么继续。" if chinese else
                  "This path is making no new progress; it needs your decision on how to go on.")
    else:
        reason = ("这条路没有新进展，团队正在换个思路。" if chinese else
                  "This path is making no new progress, so the team is trying a different approach.")
    return ReviewDecision(
        status=holder,
        reason=reason,
        next_action="",
        operator_question=reason if holder == "operator" else "",
        review_source="unchanged_return",
        host_placeholder="unchanged_return",
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
    """Hand a turn to the holder its hand_off names."""

    def _deliver(self, holder: str, *, state: RoundLoopState, label: str, climb: bool) -> tuple[str, bool]:
        """Where work handed to ``holder`` goes, and whether it climbed past any holder.

        Only work no turn handed on (``climb``) climbs: up to the operator,
        each holder that already held it as snapshot ``label`` shows it, with
        nothing new since, passes it one rung up; a Reviewer that accepted it
        as it is sends it to ``done`` instead. A hand_off goes where it names.
        The Planner and the Manager hold it from here; the Engineer and the
        Reviewer record their own hold when their turn completes.
        """
        passed = False
        while climb and holder in LADDER[:-1] and returned_unchanged(holder, state, label):
            verdict = accepted_as_is(state, label) if holder == "reviewer" else None
            if verdict:
                # This task's Reviewer holds the acceptance the work stands on.
                state.snapshots.hold("reviewer", label, outside_inputs(state), verdict)
                return "done", passed
            passed = True
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
        """Route the turn by its hand_off; ``climbed``: no turn ran, the work climbed past the Engineer."""
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
        # Work that climbed past the Engineer goes where its last turn sent it,
        # so only a handoff to the Reviewer resumes at review.
        last = state.snapshots.held_verdict("engineer") if climbed and state.snapshots else None
        wanted = (last or {}).get("holder", "reviewer") if climbed else sent_to(outcome.handoff)
        if state.snapshots is not None:
            # The Engineer held the work as its turn began, and its line sent it on.
            state.snapshots.hold("engineer", "turn", state.handed_inputs, {"holder": wanted})
        holder, passed = (
            self._deliver(wanted, state=state, label="now", climb=climbed)
            if state.snapshots is not None and state.snapshots.take("now") else (wanted, False)
        )
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
        elif passed or climbed:
            review = climb_record(holder, chinese=state.chinese)
        else:
            review = _handoff_record(holder, outcome.handoff)
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
