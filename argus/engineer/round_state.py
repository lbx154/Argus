"""Shared typed state for the ``SupervisedEngineer.run`` round loop.

``runner.py`` splits the round loop into cohesive phase mixins (prompt
assembly, engineer-turn execution, background/external waits, reviewer
invocation, and round settlement). These phases need two kinds of shared
state:

* ``RoundLoopState`` — mutable state that genuinely crosses round
  boundaries (streaks, the last reviewer ``next_action``, etc). It is
  constructed once per ``run()`` call and mutated in place by each phase;
  ``SupervisedEngineer`` itself stays stateless across calls (see its
  class docstring), so this must never become an instance attribute.
* ``EngineerTurnOutcome`` — the parsed result of a single engineer turn,
  threaded from the execution phase into the later phases of the SAME
  round.
* ``RoundControl`` — a tiny sentinel the phase methods return to tell the
  driving ``for`` loop in ``run()`` whether to return a terminal result,
  ``continue`` to the next round immediately, or fall through and let the
  loop body finish normally. This mirrors the original in-line
  ``return``/``continue`` control flow exactly; nothing about mission
  semantics changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.handoff import HandOff
from ..core.models import LoopStatus, RoundRecord
from ..core.role_session import RoleSessionCapsule
from ..core.stop_kinds import StopKind
from ..core.workspace_snapshot import WorkspaceSnapshots


@dataclass
class RoundLoopState:
    """Mutable state that persists across rounds within one ``run()`` call."""

    rounds: list[RoundRecord] = field(default_factory=list)
    # The task's workspace snapshots: its baseline, taken before the first
    # Engineer turn, the candidate each Reviewer is shown the diff to, and
    # what each holder last held the work as.
    snapshots: WorkspaceSnapshots | None = None
    # The outside inputs (``round_routing.outside_inputs``) the current
    # holder was handed the work under; recorded beside its hold.
    handed_inputs: list[object] | None = None
    # A Reviewer's verdict handed the work back to the Engineer, which has not
    # yet completed a turn on it: new input, so the Engineer's turn runs.
    review_unseen: bool = False
    # Accounts of Engineer turns that continued without any review, newest
    # last. The next Reviewer reads them beside the final turn's account; a
    # completed review clears them.
    unreviewed_engineer_accounts: list[str] = field(default_factory=list)
    last_engineer_message: str = ""
    # The project state directory the mission belongs to, if it has one.
    state_root: Path | None = None
    # The backlog item this mission works on, when its packet names one; an
    # operator's acceptance of a residual risk is recorded against it.
    mission_item_id: str = ""
    # With no item, the mission (its session) an acceptance is scoped to.
    mission_ref: str = ""
    # Resolved path -> each workspace file as recorded when the first mission on
    # this objective began (``core.grounding_baseline``). Only a file still
    # matching it grounds a Reviewer's "impossible here"; None means none does.
    grounding_baseline: dict[str, list[int]] | None = None
    reviewer_next_action: str | None = None
    backend_failure_streak: int = 0
    reviewer_backend_failure_streak: int = 0
    # Normalized signature of the most recent Engineer backend failure and the
    # count of consecutive failures sharing it. A run of identical failures is
    # one continuing cause, so the round loop holds and backs off (up to an
    # hour) instead of failing the mission into a paid replanning cycle.
    backend_failure_signature: str = ""
    backend_failure_same_cause_streak: int = 0
    # When the current same-cause run began; the hold ends after the
    # automatic-retry window.
    backend_failure_since: float = 0.0
    # Consecutive Engineer calls that each used their whole per-call
    # provider-turn allowance without a completed turn. One or two in a row are
    # routine on a long task; a run of them means the fresh sessions are not
    # converging and the mission should stop instead of burning the allowance
    # forever. Reset by any Engineer call that ends any other way.
    provider_turn_cap_streak: int = 0
    pending_secret_guard_notes: list[str] = field(default_factory=list)
    # Host-gathered round evidence (``round_evidence.py`` registry). Each
    # Engineer turn's Reviewer rendering waits here, newest last, for the next
    # completed review, which consumes all of them; the Engineer note rides in
    # the next prompt's follow-up text; ``round_evidence_state`` is each
    # provider's opaque state from its last contribution, keyed by provider,
    # handed back on the next round.
    pending_round_evidence: list[str] = field(default_factory=list)
    pending_round_evidence_engineer_note: str = ""
    round_evidence_state: dict[str, dict] = field(default_factory=dict)
    # Host text for the next Engineer prompt (the round-evidence note).
    pending_engineer_followup: str = ""
    engineer_session: RoleSessionCapsule | None = None
    # Every Engineer provider thread seen in this run (seed, each turn, each
    # wind-down). The Reviewer must never resume any of them.
    engineer_thread_ids: set[str] = field(default_factory=set)
    reviewer_session: RoleSessionCapsule | None = None


@dataclass
class EngineerTurnOutcome:
    """Parsed result of one engineer turn, handed to the later round phases."""

    engineer_result: Any
    round_thread_id: str | None
    fatal_error: str | None
    safe_fatal_error: str | None
    stop_kind: StopKind | None
    #: The turn's ``hand_off``; ``None`` when it ended without one.
    handoff: HandOff | None
    engineer_message: str
    process_ownership_note: str
    round_started_at: float


TerminalResult = tuple[LoopStatus, list[RoundRecord], str, str, str | None]


@dataclass
class RoundControl:
    """What the driving ``for`` loop in ``run()`` should do next.

    ``payload`` optionally carries a phase's non-terminal output when
    ``action == "proceed"``: the Engineer turn's outcome, or the Reviewer's
    ``ReviewDecision`` once a real (non-backend-failure) verdict is in.
    """

    action: str  # "return" | "continue_loop" | "proceed"
    terminal: TerminalResult | None = None
    payload: Any = None


def control_return(result: TerminalResult) -> RoundControl:
    return RoundControl("return", result)


def control_continue_loop() -> RoundControl:
    return RoundControl("continue_loop")


def control_proceed(payload: Any = None) -> RoundControl:
    return RoundControl("proceed", payload=payload)


__all__ = [
    "RoundLoopState",
    "EngineerTurnOutcome",
    "RoundControl",
    "TerminalResult",
    "control_return",
    "control_continue_loop",
    "control_proceed",
]
