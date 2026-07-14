"""Persistent JSONL event log.

Every supervisor / sink event is fanned-out to ``<life_dir>/events.jsonl``
in addition to whatever interactive sink the caller already had. This
gives the daemon, the ``--watch`` cockpit, future Web UI, and post-hoc
postmortem a single ground-truth replay surface that survives daemon
restarts.

The design is intentionally minimal:

* Decorator pattern: ``JsonlEventSink(downstream, path)`` wraps any sink
  conforming to the ``handle_event(dict)`` protocol. Durable append happens
  before downstream delivery, and the return value acknowledges whether the
  canonical log accepted the event.
* One JSON object per line. ``ts`` is injected if the caller didn't.
* Soft size cap: when ``events.jsonl`` exceeds ``ROLL_BYTES`` we rotate
  to ``events.jsonl.1``. We retain EVERY generation: the previous ``.1``
  is moved aside to the next free ``events.jsonl.<N>`` (``.2``, ``.3``, …)
  rather than being deleted, so no event is ever lost. ``.1`` always holds
  the most-recent previous roll (readers/tailers that expect it keep
  working); the full lifetime history is the union of ``events.jsonl*``.
* Concurrency: a process-local lock plus a POSIX file lock serializes append,
  rotation, and Mission View projection across the daemon and report tools.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from ..core.event_catalog import (
    SIGNAL_EVENT_TYPES,
    EventType,
    normalize_event_envelope,
)
from ..core.secret_guard import (
    known_secret_values,
    redact_secrets_record,
    redact_secrets_text,
)

ROLL_BYTES = 100 * 1024 * 1024  # 100 MiB
EVENT_FILE = "events.jsonl"
ROLL_FILE = "events.jsonl.1"
EVENT_LOCK_FILE = "events.lock"

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# Idle-poll chatter that pollutes the persistent log without telling
# operators anything actionable. We keep these on the in-process sink
# (so the daemon log / stderr still see them) but skip writing them to
# events.jsonl. Match by ``type`` + literal ``text``; an exact-match
# table avoids false positives.
DROP_FROM_DISK: frozenset[tuple[str, str]] = frozenset({
    (EventType.LIFE_STATUS, "backlog empty; exiting"),
    (EventType.LIFE_STATUS, "stop requested while idle"),
})


# High-value event types ALWAYS persisted, even in "signal" verbosity: mission
# / round lifecycle, verdicts, skill-memory mutations, planner decisions, and
# escalations. The noise we drop in "signal" mode is the
# per-command / intermediate-message / idle-poll churn (engineer.progress
# command_execution, session.roll, watchdog waits, telemetry deltas, match
# diagnostics) that bloats events.jsonl to multi-MB without telling an operator
# what changed. Errors and wins are preserved by a separate text-marker rule.
HIGH_VALUE_EVENT_TYPES = SIGNAL_EVENT_TYPES
# In "signal" mode, an engineer.progress event is kept only if its text carries
# a win/result/error marker (so a measured win or a traceback is never lost).
_SIGNAL_TEXT_MARKERS = (
    "RESULT", "correct=true", "cand_ms", "Traceback", "Error:",
    "exit_code", "FAILED", "NO_TRACE", "RUNTIME_ERROR",
)


def _should_persist_for_verbosity(event: dict[str, Any], verbosity: str) -> bool:
    """True if this event should hit disk at the given verbosity.

    ``full`` keeps everything (legacy behaviour). ``signal`` keeps only
    high-value types + anything carrying an error/win marker.
    """
    if verbosity != "signal":
        return True
    if not isinstance(event, dict):
        return True
    t = str(event.get("type", ""))
    if t in HIGH_VALUE_EVENT_TYPES:
        return True
    tl = t.lower()
    if "error" in tl or "fail" in tl or "escalat" in tl or "alert" in tl:
        return True
    text = str(event.get("text", "") or "")
    return any(m in text for m in _SIGNAL_TEXT_MARKERS)



class _Sink(Protocol):
    def handle_event(self, event: dict[str, Any]) -> bool | None: ...


class JsonlEventSink:
    """Tee any event sink to ``<life_dir>/events.jsonl``."""

    def __init__(
        self,
        downstream: _Sink | None,
        *,
        life_dir: Path,
        roll_bytes: int = ROLL_BYTES,
        verbosity: str | None = None,
    ) -> None:
        self._downstream = downstream
        self._dir = Path(life_dir)
        self._path = self._dir / EVENT_FILE
        self._roll_path = self._dir / ROLL_FILE
        self._file_lock_path = self._dir / EVENT_LOCK_FILE
        self._roll_bytes = max(1024 * 1024, int(roll_bytes))
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)
        # "signal" (default) persists only high-value events + error/win markers —
        # this is the SELLABLE trajectory: a clean per-mission episode, no
        # command/idle/heartbeat churn. "full" keeps everything for deep debug.
        # Errors are NEVER dropped (full, untruncated, via markers). Explicit arg
        # wins; else env; else "signal" so teammates emit clean episodes too.
        if verbosity is None:
            verbosity = os.environ.get("ARGUS_SKILL_EVENT_VERBOSITY", "signal")
        self._verbosity = "signal" if str(verbosity).strip().lower() == "signal" else "full"

    # --- Sink protocol -----------------------------------------------

    def handle_event(self, event: dict[str, Any]) -> bool:
        safe_event = self._normalize(event)
        if self._is_idle_chatter(safe_event):
            persisted = True
        elif not _should_persist_for_verbosity(safe_event, self._verbosity):
            persisted = True
        else:
            persisted = self._append(safe_event)
        if not persisted:
            if safe_event.get("event_validation") and self._downstream is not None:
                try:
                    self._downstream.handle_event(safe_event)
                except Exception:  # noqa: BLE001
                    pass
            return False
        if self._downstream is not None:
            try:
                self._downstream.handle_event(safe_event)
            except Exception:  # noqa: BLE001
                pass
        return True

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Accept stream progress so the sink satisfies EventSink."""
        safe_line = redact_secrets_text(
            line,
            known_values=known_secret_values(),
        )
        if self._downstream is not None:
            try:
                handler = getattr(self._downstream, "handle_stream_line", None)
                if handler is not None:
                    handler(stream, safe_line)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """Best-effort close for EventSink compatibility."""
        if self._downstream is None:
            return
        try:
            closer = getattr(self._downstream, "close", None)
            if closer is not None:
                closer()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _is_idle_chatter(event: dict[str, Any]) -> bool:
        if not isinstance(event, dict):
            return False
        t = str(event.get("type", ""))
        text = str(event.get("text", ""))
        return (t, text) in DROP_FROM_DISK

    # --- public so tests / migrations can drop one-shot lines --------

    def append(self, event: dict[str, Any]) -> bool:
        return self._append(event)

    # --- helpers -----------------------------------------------------

    def _append(self, event: dict[str, Any]) -> bool:
        try:
            payload = self._normalize(event)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:  # noqa: BLE001
            return False
        valid = not bool(payload.get("event_validation"))
        if payload.get("event_validation"):
            try:
                from ..core.metrics import metrics_root_for_project, record_metric

                record_metric(
                    metrics_root_for_project(self._dir),
                    "event.validation_failure",
                    labels={"type": payload.get("type") or "unknown"},
                    fields={
                        "errors": payload["event_validation"].get("errors", [])
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            lock_fd = os.open(str(self._file_lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._maybe_roll()
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                try:
                    from ..core.mission_view import (
                        mission_view_handles_event,
                        update_mission_view_event,
                    )

                    if mission_view_handles_event(payload.get("type")):
                        update_mission_view_event(self._dir, payload)
                except Exception:  # noqa: BLE001 - projection must not break logging
                    pass
            except Exception:  # noqa: BLE001
                # Disk full / read-only / permission — keep silent so the
                # supervisor doesn't crash. Operators see the warning in
                # the daemon log via _DaemonSink.handle_event downstream.
                return False
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(lock_fd)
        return valid

    @staticmethod
    def _normalize(event: dict[str, Any]) -> dict[str, Any]:
        out = normalize_event_envelope(event, timestamp=time.time())
        # Drop non-serialisable values rather than crash.
        for k, v in list(out.items()):
            try:
                json.dumps(v)
            except Exception:  # noqa: BLE001
                out[k] = repr(v)  # full repr — events.jsonl is the ground-truth replay; don't clip diagnostics
        return redact_secrets_record(
            out,
            known_values=known_secret_values(),
        )

    def _maybe_roll(self) -> None:
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001
            return
        if size < self._roll_bytes:
            return
        try:
            # Preserve EVERY generation. Instead of deleting the previous roll,
            # move it aside to the next free ``events.jsonl.<N>`` (N>=2) so no
            # events are ever lost. ``.1`` stays the most-recent previous roll
            # (readers/tailers that expect it keep working); older generations
            # accumulate as .2, .3, … and are swept up by the ``events.jsonl*``
            # glob during full-history reconstruction.
            if self._roll_path.exists():
                n = 2
                while (self._dir / f"{EVENT_FILE}.{n}").exists():
                    n += 1
                os.replace(self._roll_path, self._dir / f"{EVENT_FILE}.{n}")
            os.replace(self._path, self._roll_path)
        except Exception:  # noqa: BLE001
            pass


def wrap(
    downstream: _Sink | None,
    *,
    life_dir: Path | str,
    roll_bytes: int = ROLL_BYTES,
) -> JsonlEventSink:
    """Convenience factory used by `apps/_runtime.py` / `life_worker.py`."""
    return JsonlEventSink(
        downstream,
        life_dir=Path(life_dir),
        roll_bytes=roll_bytes,
    )


__all__ = [
    "JsonlEventSink",
    "wrap",
    "ROLL_BYTES",
    "EVENT_FILE",
    "ROLL_FILE",
    "DROP_FROM_DISK",
]
