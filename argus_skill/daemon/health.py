"""Durable semantic progress health for the lifetime daemon."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HEALTH_FILENAME = "daemon.health.json"
HEALTH_SCHEMA_VERSION = 1
# This classifies missing semantic progress for diagnostics; it never kills work.
DEFAULT_STALL_SECONDS = 30 * 60.0
_ACTIVITY_WRITE_INTERVAL_SECONDS = 5.0
_WINDOWS_REPLACE_ATTEMPTS = 6
_WINDOWS_REPLACE_INITIAL_DELAY_SECONDS = 0.01

_ACTIVE_EVENTS = frozenset({
    "life.manager.intent.started",
    "life.mission.started",
    "life.planner.start",
    "loop.start",
    "provider.request.started",
    "round.review.started",
    "round.start",
})
_IDLE_EVENTS = frozenset({
    "life.daemon.ready",
    "life.mission.completed",
    "life.mission.failed",
    "life.mission.skipped",
    "loop.done",
})
_WAITING_EVENTS = frozenset({
    "life.budget.pause",
    "life.operator_question.pending",
    "life.planner.terminal_idle",
    "life.planner.waiting",
})
_DEGRADED_EVENTS = frozenset({
    "life.daemon.degraded",
    "life.planner.error",
    "life.supervisor.error",
})
_PROGRESS_EVENTS = frozenset({
    "agent.io.complete",
    "engineer.progress",
    "life.inbox.drained",
    "life.manager.intent.completed",
    "life.daemon.ready",
    "life.mission.completed",
    "life.mission.failed",
    "life.mission.skipped",
    "life.plan.revision.committed",
    "life.planner.task_added",
    "life.planner.verdict",
    "provider.request.completed",
    "round.main.completed",
    "round.review.completed",
})


def _running_on_windows() -> bool:
    return os.name == "nt"


def _replace_atomic_file(temporary: str, path: Path) -> None:
    """Replace a health sidecar despite short-lived Windows read sharing.

    Status consumers open this file frequently. On Windows a reader or security
    scanner can briefly deny replacement with ``WinError 5/32`` even though the
    writer owns a unique temporary file. Retry only ``PermissionError`` on
    Windows, with a small bounded backoff; every other failure remains visible.
    """
    delay = _WINDOWS_REPLACE_INITIAL_DELAY_SECONDS
    for attempt in range(_WINDOWS_REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if not _running_on_windows() or attempt + 1 >= _WINDOWS_REPLACE_ATTEMPTS:
                raise
            time.sleep(delay)
            delay *= 2


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_atomic_file(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _stall_seconds() -> float:
    raw = os.environ.get("ARGUS_SKILL_DAEMON_STALL_SECONDS", "")
    try:
        return max(1.0, float(raw)) if raw else DEFAULT_STALL_SECONDS
    except ValueError:
        return DEFAULT_STALL_SECONDS


class DaemonHealthTracker:
    """Persist activity and actual progress separately for status consumers."""

    def __init__(self, life_dir: Path | str, *, pid: int | None = None) -> None:
        self.path = Path(life_dir) / HEALTH_FILENAME
        self.pid = int(pid or os.getpid())
        self._lock = threading.Lock()
        self._last_write_monotonic = time.monotonic()
        now = time.time()
        self._state: dict[str, Any] = {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "pid": self.pid,
            "phase": "starting",
            "last_event_at": now,
            "last_event": "daemon.starting",
            "last_progress_at": now,
            "last_progress_event": "daemon.starting",
        }
        _atomic_write(self.path, self._state)

    def observe(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or event.get("kind") or "").strip()
        if not kind:
            return
        now = float(event.get("ts") or time.time())
        with self._lock:
            previous_phase = self._state["phase"]
            self._state["last_event_at"] = now
            self._state["last_event"] = kind
            if kind in _DEGRADED_EVENTS:
                self._state["phase"] = "degraded"
            elif kind in _WAITING_EVENTS:
                self._state["phase"] = "waiting"
            elif kind in _IDLE_EVENTS:
                self._state["phase"] = "idle"
            elif kind in _ACTIVE_EVENTS:
                self._state["phase"] = "active"
            entered_active = (
                kind in _ACTIVE_EVENTS
                and previous_phase in {"starting", "idle", "waiting"}
            )
            if kind in _PROGRESS_EVENTS or entered_active:
                self._state["last_progress_at"] = now
                self._state["last_progress_event"] = kind
            monotonic_now = time.monotonic()
            important = (
                self._state["phase"] != previous_phase
                or kind in _PROGRESS_EVENTS
                or kind in _DEGRADED_EVENTS
            )
            if (
                important
                or monotonic_now - self._last_write_monotonic
                >= _ACTIVITY_WRITE_INTERVAL_SECONDS
            ):
                _atomic_write(self.path, self._state)
                self._last_write_monotonic = monotonic_now

    def mark_ready(self) -> None:
        self.observe({"type": "life.daemon.ready", "ts": time.time()})


def read_daemon_health(
    life_dir: Path | str,
    *,
    pid: int | None,
    alive: bool,
    now: float | None = None,
) -> dict[str, Any]:
    """Return health separately from process liveness."""
    if not alive:
        return {
            "state": "stopped",
            "stalled": False,
            "last_progress_at": None,
            "last_progress_event": "",
            "seconds_since_progress": None,
        }
    path = Path(life_dir) / HEALTH_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("health payload is not an object")
        if pid is not None and int(payload.get("pid")) != int(pid):
            raise ValueError("health pid does not match daemon pid")
        last_progress = float(payload.get("last_progress_at"))
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "state": "unknown",
            "stalled": False,
            "last_progress_at": None,
            "last_progress_event": "",
            "seconds_since_progress": None,
        }
    age = max(0.0, float(now if now is not None else time.time()) - last_progress)
    phase = str(payload.get("phase") or "unknown")
    stalled = phase == "active" and age >= _stall_seconds()
    return {
        "state": "stalled" if stalled else phase,
        "stalled": stalled,
        "last_progress_at": last_progress,
        "last_progress_event": str(payload.get("last_progress_event") or ""),
        "seconds_since_progress": age,
    }


__all__ = [
    "DEFAULT_STALL_SECONDS",
    "DaemonHealthTracker",
    "HEALTH_FILENAME",
    "read_daemon_health",
]
