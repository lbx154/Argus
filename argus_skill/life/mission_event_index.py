"""Small append-only receipt index for idempotent mission completion events.

All methods run under events_locked. Normal lookups consume only the index
suffix. A pending receipt binds the next event to one inode/offset, so a crash
between log fsync and acknowledgement needs one record read, not a history scan.
The index is derived: if lost, or a pending inode was replaced by a copied
backup, it is rebuilt once from retained canonical logs. Rebuilds scan retained
bytes once with bounded record buffers; ordinary receipts never scan history.
"""
from __future__ import annotations

import json
import os
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, TypeGuard

from ..core.jsonl_reader import MAX_JSONL_RECORD_BYTES

INDEX_FILE = "mission-events.index.jsonl"
MAX_INDEX_RECORD_BYTES = 4096


def _is_delivery_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class MissionEventIndex:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / INDEX_FILE
        self.rows: dict[str, dict[str, Any]] = {}
        self.position = 0
        self.identity: tuple[int, int] | None = None

    def refresh(self) -> None:
        if not self.path.exists():
            self._rebuild()
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity != self.identity or stat.st_size < self.position:
            self.rows = {}
            self.position = 0
            self.identity = identity
        with self.path.open("r+b") as handle:
            handle.seek(self.position)
            while line := handle.readline(MAX_INDEX_RECORD_BYTES + 1):
                if len(line) > MAX_INDEX_RECORD_BYTES:
                    raise ValueError("oversized mission event receipt index row")
                if not line.endswith(b"\n"):
                    # No event can follow an uncommitted pending receipt; a
                    # torn written receipt leaves its prior pending row intact.
                    handle.truncate(self.position)
                    handle.flush()
                    os.fsync(handle.fileno())
                    break
                row = json.loads(line)
                if (
                    not isinstance(row, dict)
                    or row.get("state") not in {"pending", "written"}
                    or not _is_delivery_id(row.get("id"))
                    or any(type(row.get(name)) is not int or row[name] < 0 for name in ("device", "inode", "offset"))
                ):
                    raise ValueError("invalid mission event receipt index")
                self.rows[str(row["id"])] = row
                self.position = handle.tell()

    def _rebuild(self) -> None:
        from ..core.mission_view._replay import sync_directory
        from .event_log import event_log_paths

        recovered: dict[str, dict[str, Any]] = {}
        for path in event_log_paths(self.root / "events.jsonl"):
            if not path.is_file():
                continue
            stat = path.stat()
            with path.open("rb") as handle:
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
                        continue
                    if b'"mission_delivery_id"' not in line or not line.endswith(b"\n"):
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeDecodeError, RecursionError):
                        continue
                    key = event.get("mission_delivery_id") if isinstance(event, dict) else None
                    if _is_delivery_id(key) and event.get("event_id") == f"mission-{key}":
                        recovered[key] = {
                            "id": key, "state": "written", "device": stat.st_dev,
                            "inode": stat.st_ino, "offset": offset,
                        }
                # A pending append may have failed at log fsync. Make any
                # recovered event durable before installing its written row.
                os.fsync(handle.fileno())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                for row in recovered.values():
                    handle.write((json.dumps(row, sort_keys=True) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        sync_directory(self.root)

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("ab") as handle:
            handle.write((json.dumps(row, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        # Refresh from the durable suffix even if a previous append fsync raised.
        self.refresh()

    def begin(self, key: str, handle: BinaryIO) -> None:
        stat = os.fstat(handle.fileno())
        self.append({
            "id": key, "state": "pending", "device": stat.st_dev,
            "inode": stat.st_ino, "offset": handle.tell(),
        })

    def finish(self, key: str) -> None:
        self.append({**self.rows[key], "state": "written"})

    def contains(self, key: str) -> bool:
        self.refresh()
        row = self.rows.get(key)
        if row is None:
            return False
        if row["state"] == "written":
            return True
        from ..core.mission_view._replay import sync_directory
        from .event_log import event_log_paths

        found_identity = False
        for path in event_log_paths(self.root / "events.jsonl"):
            if not path.is_file():
                continue
            stat = path.stat()
            if (stat.st_dev, stat.st_ino) != (row["device"], row["inode"]):
                continue
            found_identity = True
            with path.open("r+b") as handle:
                handle.seek(row["offset"])
                line = handle.readline(MAX_JSONL_RECORD_BYTES + 1)
                if len(line) > MAX_JSONL_RECORD_BYTES:
                    raise ValueError("oversized pending mission completion event")
                if not line.endswith(b"\n"):
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    continue
                if (
                    not isinstance(event, dict) or event.get("mission_delivery_id") != key
                    or event.get("event_id") != f"mission-{key}"
                ):
                    continue
                # A prior fsync failure must converge before we acknowledge it.
                os.fsync(handle.fileno())
            sync_directory(self.root)
            self.finish(key)
            return True
        if not found_identity:
            # A copied backup preserves delivery IDs but changes inode/device.
            # Rebuild only this exceptional path, then use stable IDs again.
            self._rebuild()
            self.refresh()
            recovered = self.rows.get(key)
            return recovered is not None and recovered["state"] == "written"
        return False


@lru_cache(maxsize=64)
def mission_event_index(root: str) -> MissionEventIndex:
    return MissionEventIndex(Path(root))


__all__ = ["INDEX_FILE", "mission_event_index"]
