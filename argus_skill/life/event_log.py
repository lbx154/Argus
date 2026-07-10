"""Persistent JSONL event log.

Every supervisor / sink event is fanned-out to ``<life_dir>/events.jsonl``
in addition to whatever interactive sink the caller already had. This
gives the daemon, the ``--watch`` cockpit, future Web UI, and post-hoc
postmortem a single ground-truth replay surface that survives daemon
restarts.

The design is intentionally minimal:

* Decorator pattern: ``JsonlEventSink(downstream, path)`` wraps any sink
  conforming to the ``handle_event(dict) -> None`` protocol. Calls
  through to the downstream first, then appends to disk. We never let a
  disk write failure poison the in-memory event flow.
* One JSON object per line. ``ts`` is injected if the caller didn't.
* Soft size cap: when ``events.jsonl`` exceeds ``ROLL_BYTES`` we rotate
  to ``events.jsonl.1``. We retain EVERY generation: the previous ``.1``
  is moved aside to the next free ``events.jsonl.<N>`` (``.2``, ``.3``, …)
  rather than being deleted, so no event is ever lost. ``.1`` always holds
  the most-recent previous roll (readers/tailers that expect it keep
  working); the full lifetime history is the union of ``events.jsonl*``.
* Concurrency: a process-local ``threading.Lock`` guards the append.
  Multiple processes writing to the same file is fine on Linux because
  ``open(..., "a")`` + a single short ``write()`` is atomic up to
  ``PIPE_BUF`` (4 KiB on Linux). We deliberately keep the line short.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol

ROLL_BYTES = 100 * 1024 * 1024  # 100 MiB
EVENT_FILE = "events.jsonl"
ROLL_FILE = "events.jsonl.1"

# Idle-poll chatter that pollutes the persistent log without telling
# operators anything actionable. We keep these on the in-process sink
# (so the daemon log / stderr still see them) but skip writing them to
# events.jsonl. Match by ``type`` + literal ``text``; an exact-match
# table avoids false positives.
DROP_FROM_DISK: frozenset[tuple[str, str]] = frozenset({
    ("life.status", "backlog empty; exiting"),
    ("life.status", "stop requested while idle"),
})


# High-value event types ALWAYS persisted, even in "signal" verbosity: mission
# / round lifecycle, verdicts, skill-memory mutations, planner decisions, and
# escalations. The noise we drop in "signal" mode is the
# per-command / intermediate-message / idle-poll churn (engineer.progress
# command_execution, session.roll, watchdog waits, telemetry deltas, match
# diagnostics) that bloats events.jsonl to multi-MB without telling an operator
# what changed. Errors and wins are preserved by a separate text-marker rule.
HIGH_VALUE_EVENT_TYPES: frozenset[str] = frozenset({
    "loop.start", "loop.done",
    "round.start", "round.main.completed", "round.review.completed",
    "round.escalated", "round.stall", "round.reviewer_backend_failure",
    "skill.created", "skill.updated", "skill.archived",
    "life.mission.started", "life.mission.completed",
    "life.manager.intent.started", "life.manager.intent.completed",
    "life.manager.intent.failed", "life.manager.stage_decision",
    "life.vertical.resolved",
    "life.planner.start", "life.planner.task_added",
    "life.planner.task_skipped", "life.planner.verdict",
    "life.planner.waiting", "life.planner.terminal_idle",
    "life.planner.verification_probe", "life.planner.stall_escalation",
    "life.budget.pause", "life.lifecycle.block", "life.lifecycle.transition",
    "life.inbox.queued",
    "life.daemon.idle_timeout",
    "idea.search.started", "idea.search.completed", "idea.search.skipped",
    "operator_alert",
})
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
    def handle_event(self, event: dict[str, Any]) -> None: ...


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

    def handle_event(self, event: dict[str, Any]) -> None:
        if self._downstream is not None:
            try:
                self._downstream.handle_event(event)
            except Exception:  # noqa: BLE001
                # Never let downstream failure break disk-logging path.
                pass
        if self._is_idle_chatter(event):
            return
        if not _should_persist_for_verbosity(event, self._verbosity):
            return
        self._append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Accept stream progress so the sink satisfies EventSink."""
        if self._downstream is not None:
            try:
                handler = getattr(self._downstream, "handle_stream_line", None)
                if handler is not None:
                    handler(stream, line)
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

    def append(self, event: dict[str, Any]) -> None:
        self._append(event)

    # --- helpers -----------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        try:
            payload = self._normalize(event)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            try:
                self._maybe_roll()
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:  # noqa: BLE001
                # Disk full / read-only / permission — keep silent so the
                # supervisor doesn't crash. Operators see the warning in
                # the daemon log via _DaemonSink.handle_event downstream.
                pass

    @staticmethod
    def _normalize(event: dict[str, Any]) -> dict[str, Any]:
        out = dict(event) if isinstance(event, dict) else {"raw": str(event)}
        out.setdefault("ts", time.time())
        # Drop non-serialisable values rather than crash.
        for k, v in list(out.items()):
            try:
                json.dumps(v)
            except Exception:  # noqa: BLE001
                out[k] = repr(v)  # full repr — events.jsonl is the ground-truth replay; don't clip diagnostics
        return out

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
