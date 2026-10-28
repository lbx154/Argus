"""Which mission completions the event log already holds, for idempotent delivery.

All methods run under events_locked. The index is computed from the retained
logs and kept per process; nothing is stored. The first lookup in a process
reads the retained generations once with bounded record buffers; later ones
read only appended bytes.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeGuard

from ..core.event_catalog import EventType
from ..core.jsonl_reader import MAX_JSONL_RECORD_BYTES


def _is_delivery_id(value: Any) -> TypeGuard[str]:
    # A uuid; deliveries from older releases carry 64 hex digits.
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}(?:[0-9a-f]{32})?", value) is not None


def is_mission_delivery_event(event: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(event, dict)
        and _is_delivery_id(event.get("mission_delivery_id"))
        and event.get("type") == EventType.LIFE_MISSION_COMPLETED
        and event.get("event_id") == f"mission-{event['mission_delivery_id']}"
        and not event.get("event_validation")
    )


class MissionEventIndex:
    """Where each accepted completion sits in the log, read from the log.

    Nothing is written: the index is the set of completion events found in
    the retained generations, with the file and offset of each. Each refresh
    reads only the complete lines appended since the last one. A completion
    found in newly read bytes is fsynced before it is reported, so a retry
    after a failed append acknowledges only a durable event. A log that was
    truncated, replaced or edited is read again from its start.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.rows: dict[str, dict[str, Any]] = {}
        self._consumed: dict[tuple[int, int], int] = {}

    def _reset(self) -> None:
        self.rows = {}
        self._consumed = {}

    def _scan(self, path: Path, start: int) -> int:
        """Record completions in ``path`` from ``start``; the end of its last complete line."""
        found = False
        with path.open("r+b") as handle:
            # Windows FlushFileBuffers needs a writable handle; the log is not modified.
            stat = os.fstat(handle.fileno())
            handle.seek(start)
            end = start
            while True:
                offset = handle.tell()
                line = handle.readline(MAX_JSONL_RECORD_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_JSONL_RECORD_BYTES:
                    # Skip a whole oversized row in bounded chunks so its
                    # suffix cannot masquerade as a separate event.
                    while line and not line.endswith(b"\n"):
                        line = handle.readline(MAX_JSONL_RECORD_BYTES + 1)
                    if line:
                        end = handle.tell()
                    continue
                if not line.endswith(b"\n"):
                    break  # an append in progress, or one that stopped halfway
                end = handle.tell()
                if b'"mission_delivery_id"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    continue
                if is_mission_delivery_event(event):
                    found = True
                    self.rows[event["mission_delivery_id"]] = {
                        "id": event["mission_delivery_id"], "device": stat.st_dev,
                        "inode": stat.st_ino, "offset": offset,
                    }
            if found:
                os.fsync(handle.fileno())
        return end

    def refresh(self) -> None:
        from .event_log import event_log_paths

        for path in event_log_paths(self.root / "events.jsonl"):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            identity = (stat.st_dev, stat.st_ino)
            consumed = self._consumed.get(identity, 0)
            if stat.st_size < consumed:
                self._reset()
                self.refresh()
                return
            if stat.st_size > consumed:
                self._consumed[identity] = self._scan(path, consumed)

    def _recorded(self, key: str) -> bool:
        """Whether the completion is still where it was found."""
        from .event_log import event_log_paths

        row = self.rows[key]
        for path in event_log_paths(self.root / "events.jsonl"):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if (stat.st_dev, stat.st_ino) != (row["device"], row["inode"]):
                continue
            with path.open("rb") as handle:
                handle.seek(row["offset"])
                line = handle.readline(MAX_JSONL_RECORD_BYTES + 1)
            try:
                event = json.loads(line) if line.endswith(b"\n") else None
            except (ValueError, UnicodeDecodeError, RecursionError):
                event = None
            return is_mission_delivery_event(event) and event["mission_delivery_id"] == key
        return False

    def contains(self, key: str) -> bool:
        self.refresh()
        if key not in self.rows:
            return False
        if self._recorded(key):
            return True
        # A restored or edited log: read it again from its start.
        self._reset()
        self.refresh()
        return key in self.rows


@lru_cache(maxsize=64)
def mission_event_index(root: str) -> MissionEventIndex:
    return MissionEventIndex(Path(root))


__all__ = ["mission_event_index"]
