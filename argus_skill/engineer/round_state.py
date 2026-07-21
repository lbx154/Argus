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
from .failure_signature import FailureSignature
from .self_review import EngineerCompletionDecision


@dataclass
class RoundLoopState:
    """Mutable state that persists across rounds within one ``run()`` call."""

    rounds: list[RoundRecord] = field(default_factory=list)
    last_engineer_message: str = ""
    no_progress_streak: int = 0
    semantic_stall_streak: int = 0
    plan_reconsider_streak: int = 0
    repeated_failure_streak: int = 0
    last_failure_signature: FailureSignature | None = None
    reviewer_next_action: str | None = None
    last_decision_progress_at: float = field(default_factory=lambda: time.monotonic())
    backend_failure_streak: int = 0
    reviewer_backend_failure_streak: int = 0
    pending_secret_guard_notes: list[str] = field(default_factory=list)


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
    completion_decision: EngineerCompletionDecision | None
    unmanaged_long_job_note: str
    round_started_at: float


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
