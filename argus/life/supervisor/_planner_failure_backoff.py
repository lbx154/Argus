"""Bounded retry pacing for Planner calls that keep failing.

A Planner turn can fail without anything being wrong with the daemon: the
backend errors, the reply cannot be read, or it says "not done" without a
single task. Each of those paths already settles its own cycle, but several
of them hand the daemon a zero or base-length sleep, so a Planner that keeps
failing was re-asked every few seconds until the provider's daily call cap.
Every one of those calls is billed.

This module paces the *call*, not the content. It never judges what the
Planner said; it only counts consecutive Planner turns that failed and spaces
the next turn out with capped exponential backoff and jitter. Two kinds of
failure are kept apart because they recover differently:

* **Backend failures** (the call errored, timed out, or produced nothing)
  usually clear on their own. They back off to the cap and keep re-probing at
  the cap; a configuration change wakes them at once. The operator and
  Manager supervision are told once the outage has lasted
  ``PLANNER_BACKEND_ALERT_AFTER_SECONDS``. They are never held beyond the cap.
* **Unusable decisions** (the Planner answered, but nothing usable came of
  it) repeat as long as nothing the Planner reads changes. After
  ``PLANNER_FAILURE_ALERT_AFTER`` identical ones, or once the failure became
  an operator question, the operator and Manager supervision are told once
  and the Planner waits for a change: operator input, a Manager directive,
  backlog, journal, project evidence, or Planner configuration. The wait is
  at most ``PLANNER_UNCHANGED_SKIP_MAX_SECONDS``, the same ceiling the
  unchanged-input skip uses.

Before the alert, a change in those inputs (a mission or job finishing) wakes
an unusable-decision backoff early. Operator messages always bypass every
hold. A readable decision the Host declines, a superseded turn, and an empty
plan the Manager reconciled are not failures and leave the streak alone.

The streak lives in a small state file in the project state root, so a
restart does not replay the free attempts and the alert, and parallel
supervisors of one project share one streak and one alert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from ...core.event_catalog import EventType
from ._constants import PLAN_AWAITING, PLANNER_UNCHANGED_SKIP_MAX_SECONDS

log = logging.getLogger(__name__)

# The first two failed turns keep the existing cadence (a transient fault or
# fresh operator input usually lands on the next call). From the third
# consecutive one on, the next call waits base * 2**(n-3), capped, jittered.
PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS = 2
PLANNER_FAILURE_BACKOFF_BASE_SECONDS = 30.0
PLANNER_FAILURE_BACKOFF_CAP_SECONDS = PLANNER_UNCHANGED_SKIP_MAX_SECONDS
# Identical unusable decisions in a row before the operator and Manager are told.
PLANNER_FAILURE_ALERT_AFTER = 3
# Continuous backend failure before the operator and Manager are told.
PLANNER_BACKEND_ALERT_AFTER_SECONDS = PLANNER_UNCHANGED_SKIP_MAX_SECONDS
# After the alert, unchanged inputs keep the Planner quiet at most this long.
PLANNER_FAILURE_HOLD_MAX_SECONDS = PLANNER_UNCHANGED_SKIP_MAX_SECONDS

FAILURE_KIND_BACKEND = "backend"
FAILURE_KIND_DECISION = "decision"

_STATE_FILENAME = "planner-failure-backoff.json"
_STATE_VERSION = 1

# Noise that differs between otherwise identical failures.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_ID_ASSIGN = re.compile(r"\b[\w.-]*(?:id|request|req|trace|span|call)[\w.-]*\s*[:=]\s*\S+")
_ID_TOKEN = re.compile(r"\b(?:req|request|trace|call|run|msg|chatcmpl|resp)[_-][\w-]+")
_HEXISH = re.compile(r"\b(?=[\w-]*\d)[\w-]{6,}\b")
_NUMBERS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


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


def normalize_failure_text(text: str) -> str:
    """The failure with ids, quoted values, hashes and counters removed."""
    value = " ".join(str(text or "").casefold().split())
    for pattern in (_QUOTED, _UUID, _ID_ASSIGN, _ID_TOKEN, _HEXISH):
        value = pattern.sub(" ", value)
    value = _NUMBERS.sub("#", value)
    return _SPACE.sub(" ", value).strip()[:300]


def planner_failure_kind(verdict: Any) -> str:
    """Whether the call itself failed or the Planner answered unusably."""
    if verdict is None:
        return FAILURE_KIND_BACKEND
    reason = str(getattr(verdict, "reason", "") or "").casefold()
    if reason.startswith("planner backend"):
        return FAILURE_KIND_BACKEND
    return FAILURE_KIND_DECISION


def planner_failure_key(result: Any, verdict: Any) -> str:
    """Stable identity of one failed Planner outcome.

    Request ids, quoted values, hashes and counters are dropped so backend
    noise does not make the same failure look new; when nothing is left, the
    failure's kind and outcome are its identity.
    """
    text = ""
    if verdict is not None:
        text = str(getattr(verdict, "error", "") or getattr(verdict, "reason", "") or "")
    kind = planner_failure_kind(verdict)
    normalized = normalize_failure_text(text)
    if kind == FAILURE_KIND_BACKEND:
        # Backend errors are one class: their wording is provider noise.
        normalized = ""
    blob = f"{kind}\0{result}\0{normalized}".encode("utf-8", errors="replace")
    return hashlib.sha256(blob).hexdigest()[:16]


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


class PlannerFailureBackoffMixin:
    """Paces repeated failed Planner turns; see the module docstring."""

    # -- persisted state ---------------------------------------------------

    def _planner_failure_state_path(self) -> Path:
        root = getattr(self, "_project_state_root", None)
        base = root() if callable(root) else Path(getattr(self.memory, "root", "."))
        return Path(base) / _STATE_FILENAME

    def _planner_failure_objective(self) -> str:
        return hashlib.sha256(
            str(getattr(self.config, "continuous_objective", "") or "").encode("utf-8")
        ).hexdigest()[:16]

    def _load_planner_failure_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(
                self._planner_failure_state_path().read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            payload = {}
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _STATE_VERSION
            or payload.get("objective") != self._planner_failure_objective()
        ):
            payload = {}
        self._planner_failure_streak = int(payload.get("streak", 0) or 0)
        return payload

    def _save_planner_failure_state(self, payload: dict[str, Any]) -> None:
        path = self._planner_failure_state_path()
        self._planner_failure_streak = int(payload.get("streak", 0) or 0)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not payload.get("streak"):
                path.unlink(missing_ok=True)
                return
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(
                    {
                        **payload,
                        "version": _STATE_VERSION,
                        "objective": self._planner_failure_objective(),
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError:
            log.warning("could not persist the planner failure streak", exc_info=True)

    @contextmanager
    def _planner_failure_lock(self) -> Iterator[None]:
        """Serialize streak updates between supervisors of one project."""
        import portalocker

        path = self._planner_failure_state_path().with_suffix(".lock")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
        except OSError:
            yield
            return
        try:
            try:
                portalocker.lock(handle, portalocker.LOCK_EX)
            except (OSError, portalocker.exceptions.LockException):
                pass
            yield
        finally:
            try:
                portalocker.unlock(handle)
            except (OSError, portalocker.exceptions.LockException):
                pass
            handle.close()

    def _reset_planner_failure_streak(self) -> None:
        with self._planner_failure_lock():
            self._save_planner_failure_state({})

    # -- what the Planner would read -----------------------------------------

    def _planner_failure_config_signature(self) -> str:
        """Planner backend, model and operator configuration."""
        payload: dict[str, Any] = {}
        try:
            from ...core.knobs import resolve_role_model

            payload["model"] = resolve_role_model(
                "planner", role_env="ARGUS_SKILL_PLAN_MODEL"
            )
        except Exception:  # noqa: BLE001 - other parts still sign the config
            payload["model"] = ""
        runner = getattr(self, "planner_runner", None)
        payload["backend"] = str(
            getattr(runner, "backend", "") or getattr(runner, "_backend_name", "")
            or type(runner).__name__
        )
        payload["env"] = sorted(
            (key, value)
            for key, value in os.environ.items()
            if key.startswith("ARGUS_SKILL_")
            and any(part in key for part in ("PLAN", "MODEL", "BACKEND", "REASONING"))
        )
        try:
            from ...core.paths import config_path

            global_root = getattr(self, "_budget_global_root", None)
            config_file = config_path(global_root() if callable(global_root) else None)
            payload["config"] = hashlib.sha256(config_file.read_bytes()).hexdigest()
        except Exception:  # noqa: BLE001 - a missing config file is a stable state
            payload["config"] = ""
        return _digest(payload)

    def _planner_failure_input_signature(self, state: Any) -> str:
        """Everything a fresh Planner call could act on, except configuration."""
        parts: dict[str, Any] = {}
        signer = getattr(self, "_planner_visible_input_signature", None)
        if callable(signer):
            try:
                parts["inputs"] = str(
                    signer(
                        operator_context_revision=int(
                            getattr(state, "operator_context_revision", 0) or 0
                        )
                    )
                    or ""
                )
            except Exception:  # noqa: BLE001 - other parts still sign the inputs
                parts["inputs"] = ""
        try:
            from ...core.operator_context import operator_context_state_root
            from ...manager.directive import load_active_manager_directive

            directive = load_active_manager_directive(
                operator_context_state_root(self.memory)
            )
            parts["directive"] = str(getattr(directive, "revision", "") or "")
        except Exception:  # noqa: BLE001
            parts["directive"] = ""
        evidence = getattr(self, "_manager_feedback_evidence_signature", None)
        if callable(evidence):
            try:
                parts["evidence"] = str(evidence() or "")
            except Exception:  # noqa: BLE001
                parts["evidence"] = ""
        return _digest(parts)

    # -- recording ---------------------------------------------------------

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

    def _record_planner_turn_outcome(
        self,
        state: Any,
        result: Any,
        *,
        operator_asked: bool = False,
    ) -> None:
        """Update the shared streak after a cycle that actually called the Planner."""
        if not bool(getattr(state, "planner_invoked", False)):
            return
        if self._planner_turn_was_productive(state, result):
            self._reset_planner_failure_streak()
            return
        verdict = getattr(state, "verdict", None)
        error = str(getattr(verdict, "error", "") or "") if verdict is not None else ""
        if verdict is not None and not error:
            # A readable decision the Host declined (a rejected completion,
            # only duplicate tasks) carries its own feedback and stop-loss
            # circuits; it neither extends nor clears a failure streak.
            return
        if getattr(state, "planner_turn_not_failed", False):
            # The Manager reconciled an empty plan, or a newer instruction
            # superseded the turn: neither is the Planner failing.
            return
        from ...planner import PLANNER_SUPERSEDED_ERROR

        if error.startswith(PLANNER_SUPERSEDED_ERROR):
            return
        kind = planner_failure_kind(verdict)
        key = planner_failure_key(result, verdict)
        alert: tuple[int, float, float] | None = None
        with self._planner_failure_lock():
            previous = self._load_planner_failure_state()
            now = time.time()
            streak = int(previous.get("streak", 0) or 0) + 1
            same = int(previous.get("same", 0) or 0) + 1 if previous.get("key") == key else 1
            delay = planner_failure_backoff_seconds(streak)
            alerted_at = previous.get("alerted_at")
            first_failed_at = float(previous.get("first_failed_at") or now)
            record = {
                "streak": streak,
                "same": same,
                "key": key,
                "kind": kind,
                "first_failed_at": first_failed_at,
                "last_failed_at": now,
                "not_before": now + delay,
                "alerted_at": alerted_at,
                # What the Planner would read next, after this cycle's own
                # writes (an operator-direction row, journal lines) landed.
                "signature": self._planner_failure_input_signature(state),
                "config_signature": self._planner_failure_config_signature(),
                "operator_asked": bool(operator_asked or previous.get("operator_asked")),
            }
            if alerted_at is None:
                if kind == FAILURE_KIND_BACKEND:
                    due = now - first_failed_at >= PLANNER_BACKEND_ALERT_AFTER_SECONDS
                else:
                    due = same >= PLANNER_FAILURE_ALERT_AFTER
                if operator_asked and kind == FAILURE_KIND_DECISION:
                    # The operator now holds the decision; the question is the alert.
                    record["alerted_at"] = now
                elif due:
                    record["alerted_at"] = now
                    alert = (same, delay, now - first_failed_at)
            self._save_planner_failure_state(record)
        if delay > 0:
            self._suggested_sleep_s = max(
                float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0), delay
            )
        if alert is not None:
            self._alert_repeated_planner_failure(verdict, result, kind, *alert)
        log.info(
            "planner turn failed (%s, streak=%d same=%d); next call in >= %.0fs",
            kind,
            streak,
            same,
            delay,
        )

    def _alert_repeated_planner_failure(
        self,
        verdict: Any,
        result: Any,
        kind: str,
        same: int,
        delay: float,
        failing_for: float,
    ) -> None:
        detail = " ".join(
            str(
                getattr(verdict, "error", "")
                or getattr(verdict, "reason", "")
                or result
                or ""
            ).split()
        )[:300]
        if kind == FAILURE_KIND_BACKEND:
            text = (
                f"The Planner's backend has been failing for {int(failing_for // 60)} "
                "minutes"
                + (f" ({detail})" if detail else "")
                + ". Argus keeps retrying at a slow pace; check the backend or "
                "switch the Planner model in Settings."
            )
        else:
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
            "failure_kind": kind,
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
            "failure_kind": kind,
            "consecutive_failures": same,
            "error": detail,
            "text": text,
        })
        self._emit_status(text)

    # -- holding -------------------------------------------------------------

    def _maybe_hold_failing_planner(self, state: Any) -> str | None:
        """Skip the model call while repeated failed turns are backing off.

        Runs after operator intake; a cycle that drained operator messages
        never reaches here, so guidance is always heard at once.
        """
        record = self._load_planner_failure_state()
        streak = int(record.get("streak", 0) or 0)
        if streak <= 0:
            return None
        now = time.time()
        kind = str(record.get("kind") or FAILURE_KIND_DECISION)
        alerted_at = record.get("alerted_at")
        not_before = float(record.get("not_before") or 0.0)
        if self._planner_failure_config_signature() != record.get("config_signature"):
            return None  # a new backend or model deserves a try at once
        if kind == FAILURE_KIND_BACKEND:
            hold_until = not_before
        else:
            if self._planner_failure_input_signature(state) != record.get("signature"):
                return None  # something the Planner reads has changed
            hold_until = not_before
            if alerted_at is not None:
                hold_until = max(
                    hold_until,
                    float(record.get("last_failed_at") or now)
                    + PLANNER_FAILURE_HOLD_MAX_SECONDS,
                )
        remaining = hold_until - now
        if remaining <= 0:
            return None
        self._suggested_sleep_s = max(
            float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0),
            min(remaining, PLANNER_FAILURE_BACKOFF_CAP_SECONDS),
        )
        if self._should_journal_idle_repeat("planner_failure_hold"):
            if alerted_at is not None and not record.get("operator_asked"):
                # Reported as degraded by the alert; a waiting event here
                # would make the held daemon look healthy and idle.
                self._emit_status(
                    "planner: still holding after repeated failures; "
                    "waiting for a change or the next retry"
                )
            else:
                self._emit({
                    "type": EventType.LIFE_PLANNER_WAITING,
                    "cycle": getattr(self, "_planning_cycles", 0),
                    "reason": "backing off after repeated failed Planner turns",
                    "consecutive_failures": streak,
                    "suggested_sleep_s": remaining,
                    "model_call_skipped": True,
                })
                self._emit_status(
                    "planner: holding the next call after repeated failed turns"
                )
        return PLAN_AWAITING


__all__ = [
    "FAILURE_KIND_BACKEND",
    "FAILURE_KIND_DECISION",
    "PLANNER_BACKEND_ALERT_AFTER_SECONDS",
    "PLANNER_FAILURE_ALERT_AFTER",
    "PLANNER_FAILURE_BACKOFF_BASE_SECONDS",
    "PLANNER_FAILURE_BACKOFF_CAP_SECONDS",
    "PLANNER_FAILURE_HOLD_MAX_SECONDS",
    "PlannerFailureBackoffMixin",
    "normalize_failure_text",
    "planner_failure_backoff_seconds",
    "planner_failure_kind",
    "planner_failure_key",
]
