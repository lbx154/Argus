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

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.models import LoopStatus, RoundRecord
from ..core.role_session import RoleSessionCapsule
from ..core.secret_guard import SecretScanCache


@dataclass
class RoundLoopState:
    """Mutable state that persists across rounds within one ``run()`` call."""

    rounds: list[RoundRecord] = field(default_factory=list)
    last_engineer_message: str = ""
    no_progress_streak: int = 0
    semantic_stall_streak: int = 0
    reviewer_next_action: str | None = None
    last_decision_progress_at: float = field(default_factory=lambda: time.monotonic())
    backend_failure_streak: int = 0
    reviewer_backend_failure_streak: int = 0
    # Normalized signature of the most recent Engineer backend failure and the
    # count of consecutive failures sharing it. A run of identical failures is
    # one continuing cause, so the round loop holds and backs off (up to an
    # hour) instead of failing the mission into a paid replanning cycle.
    backend_failure_signature: str = ""
    backend_failure_same_cause_streak: int = 0
    # Consecutive Engineer calls that each used their whole per-call
    # provider-turn allowance without a completed turn. One or two in a row are
    # routine on a long task; a run of them means the fresh sessions are not
    # converging and the mission should stop instead of burning the allowance
    # forever. Reset by any Engineer call that ends any other way.
    provider_turn_cap_streak: int = 0
    pending_secret_guard_notes: list[str] = field(default_factory=list)
    # A completed requested job gets one result-consumption turn per run,
    # including when it finished before the harness could enter its wait.
    external_work_resumptions: set[tuple[str, str, str]] = field(default_factory=set)
    pending_external_work_followup: str = ""
    secret_scan_cache: SecretScanCache = field(default_factory=SecretScanCache)
    engineer_session: RoleSessionCapsule | None = None
    reviewer_session: RoleSessionCapsule | None = None


@dataclass
class EngineerTurnOutcome:
    """Parsed result of one engineer turn, handed to the later round phases."""

    engineer_result: Any
    round_thread_id: str | None
    fatal_error: str | None
    safe_fatal_error: str | None
    stop_kind: str | None
    raw_engineer_message: str
    engineer_message: str
    process_ownership_note: str
    round_started_at: float
    #: The Engineer's decision event, when it recorded one. The round message
    #: below stays the human-readable narrative; control decisions are read
    #: from here so the narrative can never answer for them.
    decision: dict[str, Any] | None = None


TerminalResult = tuple[LoopStatus, list[RoundRecord], str, str, str | None]


@dataclass
class RoundControl:
    """What the driving ``for`` loop in ``run()`` should do next.

    ``payload`` optionally carries a phase's non-terminal output when
    ``action == "proceed"`` — currently only the reviewer-invocation phase
    uses it, to hand its resulting ``ReviewDecision`` to the settlement
    phase once a real (non-backend-failure) verdict has been obtained.
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
