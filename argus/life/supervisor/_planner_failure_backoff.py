"""Bounded retry pacing for Planner calls whose backend keeps failing.

Argus launches the Planner's backend, so only Argus can handle that backend
failing: the call errored, timed out, produced nothing, or Argus's own code
raised while making it. Several of those paths hand the daemon a zero or
base-length sleep, so a failing backend was re-asked every few seconds until
the provider's daily call cap. Every one of those calls is billed.

This module paces the *call*. It counts consecutive Planner turns whose call
failed and spaces the next turn out with capped exponential backoff and
jitter; a configuration change wakes it at once. The operator and Manager
supervision are told once the failure has lasted
``PLANNER_FAILURE_ALERT_AFTER_SECONDS``.

Retrying on time alone lasts ``AUTOMATIC_RETRY_WINDOW_SECONDS`` from the first
failure of a streak. After that the operator is told once and the Planner is
asked again only by an operator message or a Planner configuration change.

What the Planner said is never judged here: a turn whose backend answered
ends the streak, whatever the answer. Operator messages always bypass every
hold. A readable decision the Host declines, a superseded turn, and an empty
plan the Manager reconciled are not failures and leave the streak alone.

The streak lives in a small state file in the project state root, so a
restart does not replay the free attempts and the alert, and parallel
supervisors of one project share one streak and one alert.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import time as _lock_time  # the lock waits in real time, whatever clock paces retries
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from ...core.autonomy import AUTOMATIC_RETRY_WINDOW_HOURS, AUTOMATIC_RETRY_WINDOW_SECONDS
from ...core.event_catalog import EventType
from ...core.file_facts import file_facts
from ...core.workspace_facts import fact_text
from ._constants import PLAN_AWAITING, PLAN_RETRY

log = logging.getLogger(__name__)

# The first two failed turns keep the existing cadence (a transient fault or
# fresh operator input usually lands on the next call). From the third
# consecutive one on, the next call waits base * 2**(n-3), capped, jittered.
PLANNER_FAILURE_BACKOFF_FREE_ATTEMPTS = 2
PLANNER_FAILURE_BACKOFF_BASE_SECONDS = 30.0
PLANNER_FAILURE_BACKOFF_CAP_SECONDS = 1800.0
# Continuous failure before the operator and Manager are told.
PLANNER_FAILURE_ALERT_AFTER_SECONDS = PLANNER_FAILURE_BACKOFF_CAP_SECONDS

FAILURE_KIND_BACKEND = "backend"
# Argus's own code raised while asking the Planner: neither the backend nor
# the model is at fault.
FAILURE_KIND_INTERNAL = "internal"
_FAILURE_KINDS = frozenset({FAILURE_KIND_BACKEND, FAILURE_KIND_INTERNAL})
# The Planner handed its turn to the Manager, whose backend gave no answer: the
# turn is not over, and only Argus can ask again.
MANAGER_HANDOFF_UNANSWERED = "manager backend gave no answer to the planner handoff"
_CREDENTIAL_PARTS = ("AUTH", "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

# A clock that stepped back can leave recorded times in the future; anything
# further ahead than this is treated as "now" so a hold can never stretch.
_CLOCK_SKEW_TOLERANCE_SECONDS = 60.0
# How long a supervisor waits for another one's streak update.
_LOCK_WAIT_SECONDS = 5.0
_EVENT_SCAN_LINES = 5000

_STATE_FILENAME = "planner-failure-backoff.json"
_STATE_VERSION = 1


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


# Exceptions that only Argus's own code raises; a provider fault surfaces as
# an exit code, an OSError family member, a timeout, or a RuntimeError.
_INTERNAL_EXCEPTIONS = re.compile(
    r"^(?:KeyError|TypeError|AttributeError|IndexError|NameError|"
    r"UnboundLocalError|AssertionError|ValueError|ZeroDivisionError|"
    r"ImportError|ModuleNotFoundError|NotImplementedError|RecursionError|"
    r"LookupError)\b"
)


def planner_failure_kind(verdict: Any) -> str:
    """Whether the backend failed or Argus failed; "" when the backend answered."""
    if verdict is None:
        # The supervisor's own planning code raised before a verdict existed.
        return FAILURE_KIND_INTERNAL
    reason = str(getattr(verdict, "summary", "") or "").strip().casefold()
    error = str(getattr(verdict, "error", "") or "").strip()
    if reason.startswith("planner backend raised") and _INTERNAL_EXCEPTIONS.match(error):
        return FAILURE_KIND_INTERNAL
    if reason.startswith(("planner backend", "planner execution host", MANAGER_HANDOFF_UNANSWERED)):
        return FAILURE_KIND_BACKEND
    return ""


def _finite(value: Any) -> float:
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("not a finite number")
    return number


def _sanitized_state(payload: Any) -> dict[str, Any]:
    """The persisted streak with every field type-checked; ``{}`` if any is bad.

    A file another version, a crash or a person wrote must never block the
    Planner: an unreadable streak simply starts over.
    """
    if not isinstance(payload, dict) or not payload:
        return {}
    if str(payload.get("kind") or "") not in _FAILURE_KINDS:
        # Includes a streak of unusable answers an older version paced.
        return {}
    try:
        streak = int(payload.get("streak", 0) or 0)
        if streak <= 0:
            return {}
        alerted_at = payload.get("alerted_at")
        alert = payload.get("alert")
        if alert is not None and not isinstance(alert, dict):
            raise ValueError("alert must be an object")
        clean = {
            "streak": streak,
            "kind": str(payload["kind"]),
            "first_failed_at": _finite(payload["first_failed_at"]),
            "last_failed_at": _finite(payload["last_failed_at"]),
            "not_before": _finite(payload["not_before"]),
            "alerted_at": None if alerted_at is None else _finite(alerted_at),
            "config_signature": str(payload.get("config_signature") or ""),
            "alert": dict(alert) if alert else None,
            "alert_pending": bool(payload.get("alert_pending")),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        log.warning("ignoring an unreadable planner failure streak", exc_info=True)
        return {}
    return clean


def _clamped_to_now(record: dict[str, Any], now: float) -> bool:
    """Pull timestamps a backwards clock step left in the future back to now."""
    changed = False
    limit = now + _CLOCK_SKEW_TOLERANCE_SECONDS
    for field in ("first_failed_at", "last_failed_at", "alerted_at"):
        value = record.get(field)
        if value is not None and value > limit:
            record[field] = now
            changed = True
    ceiling = now + PLANNER_FAILURE_BACKOFF_CAP_SECONDS
    if record.get("not_before", 0.0) > ceiling:
        record["not_before"] = ceiling
        changed = True
    return changed


class PlannerFailureBackoffMixin:
    """Paces repeated failed Planner turns; see the module docstring."""

    # -- persisted state ---------------------------------------------------

    def _planner_failure_state_path(self) -> Path:
        root = getattr(self, "_project_state_root", None)
        base = root() if callable(root) else Path(getattr(self.memory, "root", "."))
        return Path(base) / _STATE_FILENAME

    def _planner_failure_objective(self) -> str:
        return str(getattr(self.config, "continuous_objective", "") or "")

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
        payload = _sanitized_state(payload)
        self._planner_failure_streak = int(payload.get("streak", 0))
        return payload

    def _save_planner_failure_state(self, payload: dict[str, Any]) -> None:
        path = self._planner_failure_state_path()
        try:
            self._planner_failure_streak = int(payload.get("streak", 0) or 0)
        except (TypeError, ValueError):
            self._planner_failure_streak = 0
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
        locked = False
        try:
            deadline = _lock_time.monotonic() + _LOCK_WAIT_SECONDS
            while True:
                try:
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    locked = True
                    break
                except (OSError, portalocker.exceptions.LockException):
                    if _lock_time.monotonic() >= deadline:
                        # A stuck peer must not stall planning; the streak is
                        # pacing advice, and the worst case is one extra call.
                        log.warning("planner failure streak lock busy; continuing unlocked")
                        break
                    _lock_time.sleep(0.05)
            yield
        finally:
            if locked:
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
        # Credentials are left out: this record is plain text on disk.
        payload["env"] = sorted(
            (key, value)
            for key, value in os.environ.items()
            if key.startswith("ARGUS_SKILL_")
            and any(part in key for part in ("PLAN", "MODEL", "BACKEND", "REASONING"))
            and not any(part in key for part in _CREDENTIAL_PARTS)
        )
        try:
            from ...core.paths import config_path

            global_root = getattr(self, "_budget_global_root", None)
            config_file = config_path(global_root() if callable(global_root) else None)
            payload["config"] = file_facts(config_file)
        except Exception:  # noqa: BLE001 - a missing config file is a stable state
            payload["config"] = ""
        return fact_text(payload)

    # -- recording ---------------------------------------------------------

    def _planner_turn_was_productive(self, state: Any, result: Any) -> bool:
        if result is True or result is False:
            return True
        if getattr(state, "added_titles", None) or getattr(state, "completion_accepted", False):
            return True
        verdict = getattr(state, "verdict", None)
        # A handoff without new work is a decision, not a failure, whether it
        # waits or the Manager's answer sends it back to plan; the
        # unchanged-input skip keeps it from being re-bought at model price.
        return bool(
            result in {PLAN_AWAITING, PLAN_RETRY}
            and verdict is not None
            and not getattr(verdict, "error", "")
            and getattr(verdict, "handoff", "")
        )

    def _record_planner_turn_outcome(self, state: Any, result: Any) -> None:
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
        if not kind:
            # The backend answered; what it said is the Planner's business.
            self._reset_planner_failure_streak()
            return
        config_signature = self._planner_failure_config_signature()
        with self._planner_failure_lock():
            previous = self._load_planner_failure_state()
            now = time.time()
            _clamped_to_now(previous, now)
            streak = int(previous.get("streak", 0)) + 1
            delay = planner_failure_backoff_seconds(streak)
            alerted_at = previous.get("alerted_at")
            first_failed_at = float(previous.get("first_failed_at") or now)
            record = {
                "streak": streak,
                "kind": kind,
                "first_failed_at": first_failed_at,
                "last_failed_at": now,
                "not_before": now + delay,
                "alerted_at": alerted_at,
                "config_signature": config_signature,
                "alert": previous.get("alert"),
                "alert_pending": bool(previous.get("alert_pending")),
            }
            if alerted_at is None and now - first_failed_at >= PLANNER_FAILURE_ALERT_AFTER_SECONDS:
                # Persisted as pending first: a crash before it is emitted
                # leaves it to be sent on the next start, never dropped.
                record["alerted_at"] = now
                record["alert_pending"] = True
                record["alert"] = {
                    "id": f"planner-alert-{uuid.uuid4().hex}",
                    "kind": kind,
                    "streak": streak,
                    "failing_for": now - first_failed_at,
                    "detail": " ".join(
                        str(
                            getattr(verdict, "error", "")
                            or getattr(verdict, "summary", "")
                            or result
                            or ""
                        ).split()
                    )[:300],
                }
            self._save_planner_failure_state(record)
        if delay > 0:
            self._suggested_sleep_s = max(
                float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0), delay
            )
        self._flush_pending_planner_alert()
        log.info(
            "planner turn failed (%s, streak=%d); next call in >= %.0fs",
            kind,
            streak,
            delay,
        )

    def _planner_alert_already_emitted(self, alert_id: str) -> bool:
        """Whether this project's event log already holds the alert."""
        path = self._planner_failure_state_path().parent / "events.jsonl"
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 256 * _EVENT_SCAN_LINES))
                tail = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return False
        return f'"alert_id":"{alert_id}"' in tail.replace(" ", "")

    def _flush_pending_planner_alert(self) -> None:
        """Emit a persisted alert exactly once, also after a crash or restart."""
        with self._planner_failure_lock():
            record = self._load_planner_failure_state()
            alert = record.get("alert")
            if not record.get("alert_pending") or not alert:
                return
            alert_id = str(alert.get("id") or "")
            if not (alert_id and self._planner_alert_already_emitted(alert_id)):
                self._alert_repeated_planner_failure(alert)
            record["alert_pending"] = False
            self._save_planner_failure_state(record)

    def _alert_repeated_planner_failure(self, alert: dict[str, Any]) -> None:
        kind = str(alert.get("kind") or FAILURE_KIND_BACKEND)
        detail = str(alert.get("detail") or "")
        try:
            streak = int(alert.get("streak") or 0)
            failing_for = float(alert.get("failing_for") or 0.0)
        except (TypeError, ValueError):
            streak, failing_for = 0, 0.0
        minutes = int(failing_for // 60)
        # The failure detail stays in ``error``; the operator reads the words.
        words = self._operator_words(
            "planner_internal_error" if kind == FAILURE_KIND_INTERNAL else "planner_failing",
            minutes=minutes,
        )
        text = words["text"]
        alert_id = str(alert.get("id") or "")
        self._emit({
            "type": EventType.LIFE_PLANNER_ERROR,
            "cycle": getattr(self, "_planning_cycles", 0),
            "error": detail or kind,
            **words,
            "operator_alert": True,
            "recoverable": True,
            "stop_kind": "planner_repeated_failure",
            "failure_kind": kind,
            "consecutive_failures": streak,
            "alert_id": alert_id,
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
            "consecutive_failures": streak,
            "error": detail,
            "text": text,
            "alert_id": alert_id,
        })
        self._emit_status(text)

    # -- holding -------------------------------------------------------------

    def _maybe_hold_failing_planner(self) -> str | None:
        """Skip the model call while repeated failed turns are backing off.

        Runs after operator intake; a cycle that drained operator messages
        never reaches here, so guidance is always heard at once.
        """
        self._flush_pending_planner_alert()
        record = self._load_planner_failure_state()
        streak = int(record.get("streak", 0))
        if streak <= 0:
            return None
        now = time.time()
        if _clamped_to_now(record, now):
            with self._planner_failure_lock():
                self._save_planner_failure_state(record)
        if self._planner_failure_config_signature() != record.get("config_signature"):
            return None  # a new backend or model deserves a try at once
        if now - float(record.get("first_failed_at")) >= AUTOMATIC_RETRY_WINDOW_SECONDS:
            return self._planner_retries_stopped(record)
        # Inside the window the call re-probes at the backoff cap at most,
        # whatever the clock or the file says.
        remaining = min(
            float(record.get("not_before")) - now, PLANNER_FAILURE_BACKOFF_CAP_SECONDS
        )
        if remaining <= 0:
            return None
        self._suggested_sleep_s = max(
            float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0), remaining
        )
        if self._should_journal_idle_repeat("planner_failure_hold"):
            if record.get("alerted_at") is not None:
                # Reported as degraded by the alert; an idle event here
                # would make the held daemon look healthy and idle.
                self._emit_status(
                    "planner: still holding after repeated failures; "
                    "waiting for a change or the next retry"
                )
            else:
                self._emit({
                    "type": EventType.LIFE_PLANNER_IDLE,
                    "cycle": getattr(self, "_planning_cycles", 0),
                    "reason": "backing off after repeated failed Planner turns",
                    "consecutive_failures": streak,
                    "model_call_skipped": True,
                })
                self._emit_status(
                    "planner: holding the next call after repeated failed turns"
                )
        return PLAN_AWAITING

    def _planner_retries_stopped(self, record: dict[str, Any]) -> str:
        """Keep a Planner that failed for the whole retry window quiet."""
        self._suggested_sleep_s = max(
            float(getattr(self, "_suggested_sleep_s", 0.0) or 0.0),
            PLANNER_FAILURE_BACKOFF_CAP_SECONDS,
        )
        notice = f"planner-retries-stopped-{int(float(record.get('first_failed_at')))}"
        told = getattr(self, "_planner_stop_notices", None)
        if told is None:
            told = self._planner_stop_notices = set()
        if notice not in told:
            told.add(notice)
            self._tell_operator(notice, (
                "planner_retries_stopped_internal"
                if record.get("kind") == FAILURE_KIND_INTERNAL
                else "planner_retries_stopped_service"
            ), hours=AUTOMATIC_RETRY_WINDOW_HOURS)
        return PLAN_AWAITING


__all__ = [
    "FAILURE_KIND_BACKEND",
    "FAILURE_KIND_INTERNAL",
    "MANAGER_HANDOFF_UNANSWERED",
    "PLANNER_FAILURE_ALERT_AFTER_SECONDS",
    "PLANNER_FAILURE_BACKOFF_BASE_SECONDS",
    "PLANNER_FAILURE_BACKOFF_CAP_SECONDS",
    "PlannerFailureBackoffMixin",
    "planner_failure_backoff_seconds",
    "planner_failure_kind",
]
