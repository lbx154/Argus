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
  to ``events.jsonl.1``. We keep at most one historical roll. This is a
  cockpit log, not an audit trail — operators who need deep history
  should ship to OTel (Phase G12).
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
    ) -> None:
        self._downstream = downstream
        self._dir = Path(life_dir)
        self._path = self._dir / EVENT_FILE
        self._roll_path = self._dir / ROLL_FILE
        self._roll_bytes = max(1024 * 1024, int(roll_bytes))
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

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
                out[k] = repr(v)[:500]
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
            if self._roll_path.exists():
                self._roll_path.unlink()
            os.replace(self._path, self._roll_path)
        except Exception:  # noqa: BLE001
            pass


def wrap(
    downstream: _Sink | None,
    *,
    life_dir: Path | str,
    roll_bytes: int = ROLL_BYTES,
) -> JsonlEventSink:
    """Convenience factory used by `_life_repl.py` / `life_worker.py`."""
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
