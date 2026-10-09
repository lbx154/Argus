"""Bounded retry pacing for Planner calls that keep coming back unusable.

A Planner turn can fail without anything being wrong with the daemon: the
backend errors, the reply cannot be read, or it says "not done" without a
single task. Each of those paths already settles its own cycle, but several
of them hand the daemon a zero or base-length sleep, so a Planner that keeps
answering the same unusable thing was re-asked every few seconds (and, where
nothing slept at all, hundreds of times a minute until the provider's daily
call cap). Every one of those calls is billed.

This module paces the *call*, not the content. It never judges what the
Planner said; it only counts consecutive Planner turns that failed (the call
errored or yielded no usable decision) and spaces the next turn out with
capped exponential backoff and jitter. Once the same failure has repeated, or
the failure was turned into an operator question, the operator and Manager
supervision are told once, and the Planner is not called again until
something it reads has changed (operator input, backlog, journal, evidence)
or a long ceiling passes. A readable decision the Host declines is left to
its own feedback circuits.
Operator messages always bypass the hold: they reach the Planner at once.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from typing import Any, Callable

from ...core.event_catalog import EventType
from ._constants import PLAN_AWAITING

log = logging.getLogger(__name__)

# The first two failed turns keep the existing cadence (a transient fault or
# fresh operator input usually lands on the next call). From the third
# consecutive one on, the next call waits base * 2**(n-3), capped, jittered.
PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS = 2
PLANNER_FAILURE_BACKOFF_BASE_SECONDS = 30.0
PLANNER_FAILURE_BACKOFF_CAP_SECONDS = 1800.0
# Identical unusable turns in a row before the operator and Manager are told.
PLANNER_FAILURE_ALERT_AFTER = 3
# After the alert, unchanged inputs keep the Planner quiet at most this long;
# a transient backend fault still gets an occasional fresh attempt.
PLANNER_FAILURE_HOLD_MAX_SECONDS = 6 * 3600.0

_NUMBERS = re.compile(r"\d+")


def planner_failure_backoff_seconds(
    consecutive: int,
    *,
    rand: Callable[[], float] = random.random,
) -> float:
    """Jittered wait before the next Planner call after ``consecutive`` misses.

    Zero while the free attempts last; then capped exponential growth with
    "equal jitter": half the nominal delay is guaranteed, the other half is
    random, so concurrent daemons do not retry in lockstep while the floor
    still bounds the call rate.
    """
    paced = int(consecutive) - PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS
    if paced <= 0:
        return 0.0
    nominal = PLANNER_FAILURE_BACKOFF_BASE_SECONDS * (2 ** min(paced - 1, 20))
    nominal = min(PLANNER_FAILURE_BACKOFF_CAP_SECONDS, nominal)
    jitter = min(1.0, max(0.0, float(rand())))
    return nominal / 2.0 + jitter * nominal / 2.0


def planner_failure_key(result: Any, verdict: Any) -> str:
    """Stable identity of one unusable Planner outcome.

    Numbers are dropped so a retry counter or a cycle number inside the error
    text does not make the same failure look new.
    """
    text = ""
    if verdict is not None:
        text = str(getattr(verdict, "error", "") or getattr(verdict, "reason", "") or "")
    text = _NUMBERS.sub("#", " ".join(text.casefold().split()))[:400]
    blob = f"{result}\0{text}".encode("utf-8", errors="replace")
    return hashlib.sha256(blob).hexdigest()[:16]


class PlannerFailureBackoffMixin:
    """Paces repeated unusable Planner turns; see the module docstring."""

    def _planner_turn_was_productive(self, state: Any, result: Any) -> bool:
        if result is True or result is False:
            return True
        if getattr(state, "added_titles", None) or getattr(state, "completion_accepted", False):
            return True
        verdict = getattr(state, "verdict", None)
        # An intentional wait is a decision, not a failure; the unchanged-input
        # skip already keeps such a wait from being re-bought at model price.
        return bool(
            result == PLAN_AWAITING
            and verdict is not None
            and not getattr(verdict, "error", "")
            and getattr(verdict, "waiting", False)
        )

    def _reset_planner_failure_streak(self) -> None:
        self._planner_failure_streak = 0
        self._planner_failure_same = 0
        self._planner_failure_key = ""
        self._planner_failure_not_before = 0.0
        self._planner_failure_alerted_at = None
        self._planner_failure_signature = ""

    def _record_planner_turn_outcome(
        self,
        state: Any,
        result: Any,
        *,
        operator_asked: bool = False,
    ) -> None:
        """Update the streak after a cycle that actually called the Planner."""
        if not bool(getattr(state, "planner_invoked", False)):
            return
        if self._planner_turn_was_productive(state, result):
            self._reset_planner_failure_streak()
            return
        verdict = getattr(state, "verdict", None)
        if verdict is not None and not str(getattr(verdict, "error", "") or ""):
            # A readable decision the Host declined (a rejected completion,
            # only duplicate tasks) carries its own feedback and stop-loss
            # circuits; it neither extends nor clears a failure streak.
            return
        key = planner_failure_key(result, verdict)
        streak = int(getattr(self, "_planner_failure_streak", 0) or 0) + 1
        same = (
            int(getattr(self, "_planner_failure_same", 0) or 0) + 1
            if key == str(getattr(self, "_planner_failure_key", "") or "")
            else 1
        )
        self._planner_failure_streak = streak
        self._planner_failure_same = same
        self._planner_failure_key = key
        now = time.monotonic()
        delay = planner_failure_backoff_seconds(streak)
        self._planner_failure_not_before = now + delay
        if delay > 0:
            self._suggested_sleep_s = max(
                float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0), delay
            )
        # What the Planner would read next, after this cycle's own writes
        # (an operator-direction row, journal lines) have landed.
        self._planner_failure_signature = self._planner_failure_input_signature(state)
        already_alerted = getattr(self, "_planner_failure_alerted_at", None) is not None
        if already_alerted:
            # A re-probe that fails the same way restarts the quiet window
            # without telling the operator the same thing again.
            self._planner_failure_alerted_at = now
        elif operator_asked:
            # The operator now holds the decision; the alert is the question.
            self._planner_failure_alerted_at = now
        elif same >= PLANNER_FAILURE_ALERT_AFTER:
            self._planner_failure_alerted_at = now
            self._alert_repeated_planner_failure(verdict, result, same, delay)
        log.info(
            "planner turn committed no work (streak=%d same=%d); next call in >= %.0fs",
            streak,
            same,
            delay,
        )

    def _planner_failure_input_signature(self, state: Any) -> str:
        signer = getattr(self, "_planner_visible_input_signature", None)
        if not callable(signer):
            return ""
        try:
            return str(
                signer(
                    operator_context_revision=int(
                        getattr(state, "operator_context_revision", 0) or 0
                    )
                )
                or ""
            )
        except Exception:  # noqa: BLE001 - no signature only disables the hold
            log.debug("planner failure signature unavailable", exc_info=True)
            return ""

    def _alert_repeated_planner_failure(
        self,
        verdict: Any,
        result: Any,
        same: int,
        delay: float,
    ) -> None:
        detail = " ".join(
            str(
                getattr(verdict, "error", "")
                or getattr(verdict, "reason", "")
                or result
                or ""
            ).split()
        )[:300]
        text = (
            f"The Planner returned the same unusable answer {same} times in a row"
            + (f" ({detail})" if detail else "")
            + ". Argus stopped re-asking it and will try again when something "
            "changes: send guidance, change the task, or fix the backend."
        )
        self._emit({
            "type": EventType.LIFE_PLANNER_ERROR,
            "cycle": getattr(self, "_planning_cycles", 0),
            "error": detail or str(result),
            "operator_alert": True,
            "recoverable": True,
            "stop_kind": "planner_repeated_failure",
            "consecutive_failures": same,
            "suggested_sleep_s": delay,
        })
        # Manager supervision listens for degraded-daemon evidence; this is
        # the one place the repeat becomes its decision, not another retry.
        self._emit({
            "type": EventType.LIFE_DAEMON_DEGRADED,
            "health": "degraded",
            # The campaign objective is live; its next step is what failed.
            "objective_dispatched": True,
            "reason": "planner_repeated_failure",
            "agent_layer": "planner",
            "consecutive_failures": same,
            "error": detail,
            "text": text,
        })
        self._emit_status(text)

    def _maybe_hold_failing_planner(self, state: Any) -> str | None:
        """Skip the model call while repeated unusable turns are backing off.

        Runs after operator intake; a cycle that drained operator messages
        never reaches here, so guidance is always heard at once.
        """
        streak = int(getattr(self, "_planner_failure_streak", 0) or 0)
        if streak <= 0:
            return None
        now = time.monotonic()
        not_before = float(getattr(self, "_planner_failure_not_before", 0.0) or 0.0)
        alerted_at = getattr(self, "_planner_failure_alerted_at", None)
        remaining = not_before - now
        held_by_alert = False
        if remaining <= 0 and alerted_at is not None:
            recorded = str(getattr(self, "_planner_failure_signature", "") or "")
            current = str(getattr(state, "planner_input_signature", "") or "")
            if not current:
                current = self._planner_failure_input_signature(state)
            held_by_alert = bool(
                recorded
                and current == recorded
                and now - float(alerted_at) < PLANNER_FAILURE_HOLD_MAX_SECONDS
            )
        if remaining <= 0 and not held_by_alert:
            return None
        wait = (
            remaining
            if remaining > 0
            else planner_failure_backoff_seconds(
                max(streak, PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS + 1)
            )
        )
        self._suggested_sleep_s = max(
            float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0), wait
        )
        if self._should_journal_idle_repeat("planner_failure_hold"):
            self._emit({
                "type": EventType.LIFE_PLANNER_WAITING,
                "cycle": getattr(self, "_planning_cycles", 0),
                "reason": (
                    "waiting for a change before asking the Planner again"
                    if held_by_alert
                    else "backing off after repeated unusable Planner answers"
                ),
                "consecutive_failures": streak,
                "suggested_sleep_s": wait,
                "model_call_skipped": True,
            })
            self._emit_status(
                "planner: holding the next call after repeated unusable answers"
                + (" until something changes" if held_by_alert else "")
            )
        return PLAN_AWAITING


__all__ = [
    "PLANNER_FAILURE_ALERT_AFTER",
    "PLANNER_FAILURE_BACKOFF_BASE_SECONDS",
    "PLANNER_FAILURE_BACKOFF_CAP_SECONDS",
    "PLANNER_FAILURE_HOLD_MAX_SECONDS",
    "PlannerFailureBackoffMixin",
    "planner_failure_backoff_seconds",
    "planner_failure_key",
]
