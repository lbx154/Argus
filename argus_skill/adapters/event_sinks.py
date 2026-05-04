"""Event sinks: where structured events go OUT.

Provenance: shaped after ``ArgusBot/codex_autoloop/adapters/event_sinks.py``,
trimmed to three sinks:

  * ``TerminalEventSink`` — print to stderr.
  * ``TelegramEventSink`` — forward to ``TelegramNotifier`` (sendMessage
    + send_local_file for ``final.report.ready`` events).
  * ``JsonlEventSink`` — append to a JSONL file (for tests + audit log).

A daemon typically runs ``CompositeEventSink([Terminal, Telegram, Jsonl])``.
"""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..core.ports import EventSink
from ..telegram.notifier import TelegramNotifier


class CompositeEventSink:
    def __init__(self, sinks: Iterable[EventSink]) -> None:
        self._sinks = list(sinks)

    def handle_event(self, event: dict[str, object]) -> None:
        for sink in self._sinks:
            try:
                sink.handle_event(event)
            except Exception:  # noqa: BLE001 — never let one sink kill another
                pass

    def handle_stream_line(self, stream: str, line: str) -> None:
        for sink in self._sinks:
            try:
                sink.handle_stream_line(stream, line)
            except Exception:  # noqa: BLE001
                pass

    def set_verbose(self, verbose: bool) -> None:
        """Forward verbose toggle to any sink that supports it."""
        for sink in self._sinks:
            setter = getattr(sink, "set_verbose", None)
            if callable(setter):
                try:
                    setter(verbose)
                except Exception:  # noqa: BLE001
                    pass

    def close(self) -> None:
        for sink in reversed(self._sinks):
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                pass


@dataclass
class TerminalEventSink:
    verbose: bool = True

    def handle_event(self, event: dict[str, object]) -> None:
        kind = str(event.get("type", "?"))
        text = str(event.get("text", ""))
        sys.stderr.write(f"[{kind}] {text}\n")
        sys.stderr.flush()

    def handle_stream_line(self, stream: str, line: str) -> None:
        if not self.verbose:
            return
        sys.stderr.write(f"[{stream}] {line}\n")
        sys.stderr.flush()

    def close(self) -> None:
        return


class TelegramEventSink:
    def __init__(self, *, notifier: TelegramNotifier) -> None:
        self.notifier = notifier
        self._closed = False

    def handle_event(self, event: dict[str, object]) -> None:
        if self._closed:
            return
        event_type = str(event.get("type", ""))
        if event_type == "final.report.ready":
            raw_path = str(event.get("path") or "").strip()
            if raw_path:
                path = Path(raw_path)
                if path.exists():
                    self.notifier.send_local_file(path, caption="argus-skill final report")
        self.notifier.notify_event(event)

    def handle_stream_line(self, stream: str, line: str) -> None:
        # We deliberately don't forward every stream line — too noisy.
        return

    def set_verbose(self, verbose: bool) -> None:
        """Toggle the chat-side event filter (forwards to the notifier)."""
        self.notifier.set_verbose(verbose)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.notifier.close()


class JsonlEventSink:
    """Append every event as one JSONL line. Mainly for tests + audit.

    Rotates the file when it grows beyond ``rotate_max_bytes`` (default
    5 MB), keeping up to ``rotate_keep`` historical copies as
    ``<name>.1``, ``<name>.2`` ... so a long-running daemon doesn't
    end up with an ever-growing log.
    """

    DEFAULT_ROTATE_MAX_BYTES = 5 * 1024 * 1024
    DEFAULT_ROTATE_KEEP = 3

    def __init__(
        self,
        path: str | Path,
        *,
        rotate_max_bytes: int | None = None,
        rotate_keep: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rotate_max_bytes = (
            self.DEFAULT_ROTATE_MAX_BYTES if rotate_max_bytes is None else rotate_max_bytes
        )
        self.rotate_keep = self.DEFAULT_ROTATE_KEEP if rotate_keep is None else rotate_keep
        self._lock = threading.Lock()

    def handle_event(self, event: dict[str, object]) -> None:
        record = {"event": event}
        self._append(record)

    def handle_stream_line(self, stream: str, line: str) -> None:
        self._append({"stream": stream, "line": line})

    def close(self) -> None:
        return

    def _append(self, record: dict) -> None:
        with self._lock:
            self._maybe_rotate()
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _maybe_rotate(self) -> None:
        if self.rotate_max_bytes <= 0 or self.rotate_keep <= 0:
            return
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size < self.rotate_max_bytes:
            return
        # Shift backups: <name>.(keep-1) -> <name>.keep, ..., <name> -> <name>.1
        for i in range(self.rotate_keep, 0, -1):
            src = (
                self.path.with_suffix(self.path.suffix + f".{i - 1}") if i > 1 else self.path
            )
            dst = self.path.with_suffix(self.path.suffix + f".{i}")
            try:
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
            except OSError:
                # Rotation is best-effort. If it fails, keep appending.
                return
