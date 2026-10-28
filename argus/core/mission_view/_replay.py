"""The Mission View, computed from the canonical event log on read.

``events.jsonl`` (with its retained generations) is the only record. The view
is a pure function of it and is not written anywhere: each process keeps the
views it has computed, with the byte position consumed, in a bounded cache and
on the next read consumes only the bytes appended since. A view that is not
cached is rebuilt from the first retained generation. Readers hold events.lock
while they read the log, so they never see half an append.
"""
from __future__ import annotations

import copy
import os
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..event_catalog import EventType, canonical_event_type, validate_event_envelope
from ..file_lock import exclusive_file_lock
from ..json_codec import loads_finite_json
from ._dispatch import reduce_mission_view_event
from ._view_state import empty_mission_view, mission_view_handles_event

REPLAY_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024 * 1024
CURSOR_KEY = "_event_cursor"
# Views of this many projects stay computed per process; an evicted one is
# rebuilt from its log on its next read.
MAX_CACHED_VIEWS = 256

_VIEWS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_VIEW_LOCKS: dict[str, threading.Lock] = {}
_CACHE_GUARD = threading.Lock()


class MissionViewReplayError(RuntimeError):
    """The event history the cached view was computed from is gone."""


@contextmanager
def events_locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "events.lock").open("a+b") as handle:
        with exclusive_file_lock(handle, lock_name="event log"):
            yield


def sync_directory(root: Path) -> None:
    """Persist file creation/rename on POSIX; Windows lacks directory fsync."""
    if os.name == "nt":
        return
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _paths(root: Path) -> list[Path]:
    # Lazy import keeps the dependency out of module initialization. This is the
    # same chronological generation ordering used by the canonical log readers.
    from ...life.event_log import event_log_paths

    return event_log_paths(root / "events.jsonl")


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _identity(stat: os.stat_result) -> tuple[int, int]:
    return stat.st_dev, stat.st_ino


def _bytes_at(handle, start: int, length: int) -> str:
    """The bytes at ``start``, as hex, to recognise the consumed prefix again."""
    handle.seek(start)
    return handle.read(length).hex()


def _source(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        return {
            "name": path.name, "device": stat.st_dev, "inode": stat.st_ino,
            "offset": offset, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "head": _bytes_at(handle, 0, min(offset, 256)),
            "tail": _bytes_at(handle, max(0, offset - 128), min(offset, 128)),
        }


def _matches_content(path: Path, source: dict[str, Any]) -> bool:
    stat = _stat(path)
    if stat is None or stat.st_size < source["offset"]:
        return False
    offset = source["offset"]
    with path.open("rb") as handle:
        return (
            _bytes_at(handle, 0, min(offset, 256)) == source["head"]
            and _bytes_at(handle, max(0, offset - 128), min(offset, 128)) == source["tail"]
        )


def reconcile_unlocked(root: Path, view: dict[str, Any]) -> dict[str, Any]:
    """Consume a bounded suffix of the log while the caller holds events.lock."""
    cursor: dict[str, Any] | None = view.get(CURSOR_KEY)
    source = cursor.get("source") if cursor else None
    current = root / "events.jsonl"
    current_stat = _stat(current)
    paths: list[Path] | None = None
    reason = ""

    if cursor and source is None and current_stat is None:
        # A normal writer always creates events.jsonl. Checking the first
        # retained generations also notices archives without enumerating the
        # directory.
        if not (root / "events.jsonl.1").exists() and not (root / "events.jsonl.2").exists():
            return view
    if source and current_stat is None and view.get("projection_sync", {}).get("status") == "current":
        archived_stat = _stat(root / source["name"])
        if (
            archived_stat is not None
            and _identity(archived_stat) == (source["device"], source["inode"])
            and archived_stat.st_size == source["size"]
            and archived_stat.st_mtime_ns == source["mtime_ns"]
        ):
            return view
    if cursor and source and current_stat is not None and _identity(current_stat) == (source["device"], source["inode"]):
        if (
            current_stat.st_size == source["size"]
            and current_stat.st_mtime_ns == source["mtime_ns"]
            and (
                (source["offset"] == source["size"] and not cursor.get("skipping_oversized_row"))
                or view.get("projection_sync", {}).get("status") == "waiting_for_line"
            )
        ):
            # No directory scan, log read or reduction on the common polling
            # path (also covers an unchanged partial last line).
            return view
        if _matches_content(current, source):
            paths = [current]
        else:
            reason = "log_truncated" if current_stat.st_size < source["offset"] else "log_replaced"
    if paths is None:
        paths = _paths(root)
        if source and not reason:
            matches = [path for path in paths if _identity(path.stat()) == (source["device"], source["inode"])]
            matches = [path for path in matches if _matches_content(path, source)]
            if not matches and not source["offset"]:
                # An empty generation has no consumed bytes to verify. A
                # restored file with the same generation name resumes at zero.
                matches = [path for path in paths if path.name == source["name"]]
            if not matches and source["offset"]:
                # Backups/copies change inodes, but a matching head and cursor
                # boundary retain the same logical prefix and can resume safely.
                matches = [path for path in paths if _matches_content(path, source)]
            if len(matches) == 1:
                paths = paths[paths.index(matches[0]):]
            else:
                reason = "log_history_changed"

    if reason and not paths:
        raise MissionViewReplayError("Mission View cursor refers to missing event history")

    if cursor is None or reason:
        if reason or any(path.stat().st_size for path in paths):
            view = empty_mission_view()
        cursor = {"version": 1, "source": None, "last_event_id": ""}
        source = None
    skipped = int(view.get("projection_sync", {}).get("skipped_rows", 0))
    oversized = int(view.get("projection_sync", {}).get("oversized_rows", 0))
    consumed = 0
    waiting = False
    processed_path: Path | None = None
    offset = source["offset"] if source else 0
    for index, path in enumerate(paths):
        processed_path = path
        if index:
            offset = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            while consumed < REPLAY_BYTES:
                if cursor.get("skipping_oversized_row"):
                    # A large JSON line is retained in the audit log but is not
                    # a reason to allocate arbitrary memory or block later rows.
                    raw = handle.readline(min(REPLAY_BYTES, MAX_EVENT_BYTES) + 1)
                    consumed += len(raw)
                    offset = handle.tell()
                    if raw.endswith(b"\n") or (not raw and path != current):
                        cursor["skipping_oversized_row"] = False
                    if not raw:
                        waiting = path == current
                        break
                    continue
                raw = handle.readline(MAX_EVENT_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_EVENT_BYTES:
                    consumed += len(raw)
                    offset = handle.tell()
                    cursor["skipping_oversized_row"] = not raw.endswith(b"\n")
                    cursor["last_event_id"] = f"oversized:{path.name}:{offset}"
                    skipped += 1
                    oversized += 1
                    continue
                if not raw.endswith(b"\n") and path == current:
                    waiting = True
                    break
                consumed += len(raw)
                offset = handle.tell()
                cursor["last_event_id"] = f"line:{path.name}:{offset}"
                try:
                    event = loads_finite_json(raw)
                except (UnicodeDecodeError, ValueError):
                    skipped += 1
                    continue
                if not isinstance(event, dict):
                    skipped += 1
                    continue
                cursor["last_event_id"] = str(event.get("event_id") or cursor["last_event_id"])
                if event.get("event_validation") or not validate_event_envelope(
                    event, allow_missing_fields=True,
                ).valid:
                    # Invalid envelopes remain in the audit log. They cannot
                    # become a poison record that blocks every later projection.
                    skipped += 1
                    continue
                kind = canonical_event_type(event.get("type"))
                stale_review = (
                    kind in {EventType.ROUND_REVIEW_STARTED, EventType.ROUND_REVIEW_COMPLETED}
                    and event.get("item_id") not in (None, "", view.get("mission", {}).get("id", ""))
                )
                # A late prior-task review must not overwrite the new owner.
                if mission_view_handles_event(kind) and not stale_review:
                    reduce_mission_view_event(view, event)
        cursor["source"] = _source(path, offset)
        if consumed >= REPLAY_BYTES or waiting:
            break

    caught_up = not waiting and (not paths or bool(
        processed_path == paths[-1]
        and offset == paths[-1].stat().st_size
        and not cursor.get("skipping_oversized_row")
    ))
    view["bootstrapped"] = True
    view[CURSOR_KEY] = cursor
    view["projection_sync"] = {
        "status": "current" if caught_up else "waiting_for_line" if waiting else "catching_up",
        "last_event_id": cursor.get("last_event_id", ""),
        "skipped_rows": skipped,
        "oversized_rows": oversized,
        "reset_reason": reason or view.get("projection_sync", {}).get("reset_reason", ""),
    }
    return view


def _view_lock(key: str) -> threading.Lock:
    with _CACHE_GUARD:
        return _VIEW_LOCKS.setdefault(key, threading.Lock())


def load_reconciled_view(root: Path) -> dict[str, Any]:
    """The project's Mission View as of the end of its log (a private copy)."""
    key = str(root.resolve())
    with events_locked(root), _view_lock(key):
        with _CACHE_GUARD:
            cached = _VIEWS.get(key)
        # Reduce into a copy: a pass that fails part-way leaves the cached
        # view and its cursor as they were, and the next read retries it.
        view = copy.deepcopy(cached) if cached is not None else empty_mission_view()
        # Each pass reads a bounded number of bytes; a cold view takes as
        # many passes as its history needs.
        view = reconcile_unlocked(root, view)
        while view["projection_sync"]["status"] == "catching_up":
            view = reconcile_unlocked(root, view)
        with _CACHE_GUARD:
            _VIEWS[key] = view
            _VIEWS.move_to_end(key)
            while len(_VIEWS) > MAX_CACHED_VIEWS:
                _VIEWS.popitem(last=False)
        return copy.deepcopy(view)


def forget_cached_views() -> None:
    """Drop every computed view; the next read rebuilds from the log."""
    with _CACHE_GUARD:
        _VIEWS.clear()
