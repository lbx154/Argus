"""Incremental reconciliation of the canonical event log and its read model.

The view and consumed byte position are one atomic checkpoint. A failed write
replays the same suffix against the previous view, never against partial state.
All participants acquire events.lock before mission-view.lock; no callback runs
while the event writer owns events.lock. Work per pass is bounded by the byte
budget plus one complete event, including initial/legacy reconstruction.
"""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..event_catalog import EventType, canonical_event_type, validate_event_envelope
from ..file_lock import exclusive_file_lock
from ..json_codec import loads_finite_json
from ._dispatch import reduce_mission_view_event
from ._view_state import (
    _locked,
    _read_unlocked,
    _write_unlocked,
    empty_mission_view,
    mission_view_handles_event,
)

REPLAY_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 16 * 1024 * 1024
CURSOR_KEY = "_event_cursor"


class MissionViewReplayError(RuntimeError):
    """The persisted log/checkpoint cannot be safely consumed."""


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


def log_checkpoint(root: Path) -> dict[str, Any]:
    path = root / "events.jsonl"
    if not path.exists():
        paths = _paths(root)
        path = paths[-1] if paths else path
    source = _source(path, path.stat().st_size) if path.exists() else None
    return {"version": 1, "source": source, "last_event_id": ""}


def _canonical_append_after_direct_update(root: Path, view: dict[str, Any]) -> bool:
    cursor = _cursor({CURSOR_KEY: view.get("_unlogged_log_cursor")})
    source = cursor.get("source") if cursor else None
    current = root / "events.jsonl"
    stat = _stat(current)
    paths = None
    if source and stat is not None and _identity(stat) == (source["device"], source["inode"]):
        if source["offset"] == stat.st_size and source["mtime_ns"] == stat.st_mtime_ns:
            return False
        if _matches_content(current, source):
            paths = [current]
    if paths is None:
        paths = _paths(root)
        if source:
            matches = [path for path in paths if _identity(path.stat()) == (source["device"], source["inode"]) and _matches_content(path, source)]
            if not matches and source["offset"]:
                matches = [path for path in paths if _matches_content(path, source)]
            if len(matches) == 1:
                paths = paths[paths.index(matches[0]):]
            else:
                source = None
    # Search from the saved baseline, not just the newest line: a raw/legacy
    # writer can append after a canonical event whose projection callback failed.
    marker = b',"log_writer_version":1}\n'
    remaining = REPLAY_BYTES
    carry = bytes.fromhex(str((cursor or {}).get("probe_tail") or ""))
    offset = source["offset"] if source else 0
    for index, path in enumerate(paths):
        if index:
            offset, carry = 0, b""
        with path.open("rb") as handle:
            handle.seek(offset)
            chunk = handle.read(remaining)
        if marker in carry + chunk:
            return True
        carry = (carry + chunk)[-(len(marker) - 1):]
        offset += len(chunk)
        remaining -= len(chunk)
        more = offset < path.stat().st_size or index < len(paths) - 1
        if not remaining and more:
            view["_unlogged_log_cursor"] = {
                "version": 1, "source": _source(path, offset),
                "last_event_id": "", "probe_tail": carry.hex(),
            }
            view["projection_sync"]["probe_pending"] = True
            _write_unlocked(root, view)
            return False
    if view.get("projection_sync", {}).get("probe_pending"):
        view["_unlogged_log_cursor"] = log_checkpoint(root)
        view["projection_sync"].pop("probe_pending")
        _write_unlocked(root, view)
    # Small legacy-only tails preserve the existing projection bytes. Large
    # probes checkpoint their progress so restarts still make bounded progress.
    return False


def _digest(handle, start: int, length: int) -> str:
    handle.seek(start)
    return hashlib.sha256(handle.read(length)).hexdigest()


def _source(path: Path, offset: int) -> dict[str, Any]:
    with path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        return {
            "name": path.name, "device": stat.st_dev, "inode": stat.st_ino,
            "offset": offset, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "head": _digest(handle, 0, min(offset, 256)),
            "tail": _digest(handle, max(0, offset - 128), min(offset, 128)),
        }


def _matches_content(path: Path, source: dict[str, Any]) -> bool:
    stat = _stat(path)
    if stat is None or stat.st_size < source["offset"]:
        return False
    offset = source["offset"]
    with path.open("rb") as handle:
        return (
            _digest(handle, 0, min(offset, 256)) == source["head"]
            and _digest(handle, max(0, offset - 128), min(offset, 128)) == source["tail"]
        )


def _cursor(view: dict[str, Any]) -> dict[str, Any] | None:
    value = view.get(CURSOR_KEY)
    if value is None:
        return None
    try:
        if not isinstance(value, dict) or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("unsupported cursor")
        source = value["source"]
        if source is not None:
            if (
                not isinstance(source, dict)
                or not isinstance(source["name"], str)
                or re.fullmatch(r"events\.jsonl(?:\.[1-9][0-9]*)?", source["name"]) is None
            ):
                raise ValueError("invalid source")
            for name in ("device", "inode", "offset", "size", "mtime_ns"):
                if type(source[name]) is not int or source[name] < 0:
                    raise ValueError("invalid file position")
            if source["offset"] > source["size"]:
                raise ValueError("cursor exceeds observed file size")
            for name in ("head", "tail"):
                if not isinstance(source[name], str) or len(source[name]) != 64:
                    raise ValueError("invalid content identity")
                int(source[name], 16)
        if not isinstance(value.get("last_event_id"), str):
            raise ValueError("invalid event identity")
        if type(value.get("skipping_oversized_row", False)) is not bool:
            raise ValueError("invalid oversized-row progress")
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionViewReplayError("invalid Mission View event cursor") from exc


def _preserve_legacy_details(view: dict[str, Any]) -> None:
    """Keep older display fields only when replay establishes the same mission."""
    legacy = view.pop("_projection_legacy", None)
    if not legacy:
        return
    old, current = legacy["mission"], view["mission"]
    if (
        not old.get("id") or old.get("id") != current.get("id")
        or old.get("completed_at") != current.get("completed_at")
        or (current.get("started_at") is not None and old.get("started_at") != current["started_at"])
    ):
        return
    for key in ("summary", "final_output"):
        if not current.get(key):
            current[key] = old.get(key) or ""
    if not view["stage"].get("id"):
        view["stage"] = legacy["stage"]


def reconcile_unlocked(root: Path, view: dict[str, Any], *, force_logged: bool = False) -> dict[str, Any]:
    """Consume a bounded suffix while the caller holds both locks in order."""
    unlogged = view.get("projection_sync", {}).get("status") == "unlogged"
    if unlogged and not force_logged and not _canonical_append_after_direct_update(root, view):
        return view
    cursor = None if unlogged else _cursor(view)
    source = cursor.get("source") if cursor else None
    current = root / "events.jsonl"
    current_stat = _stat(current)
    paths: list[Path] | None = None
    reason = ""

    if cursor and source is None and current_stat is None:
        # A normal writer always creates events.jsonl. Checking the first
        # retained generations also notices legacy archives without enumerating
        # the directory or rewriting an idle, empty project's checkpoint.
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
    if source and cursor is not None and current_stat is not None and _identity(current_stat) == (source["device"], source["inode"]):
        if (
            current_stat.st_size == source["size"]
            and current_stat.st_mtime_ns == source["mtime_ns"]
            and (
                (source["offset"] == source["size"] and not cursor.get("skipping_oversized_row"))
                or view.get("projection_sync", {}).get("status") == "waiting_for_line"
            )
        ):
            # No directory scan, log read, reduction, or checkpoint write on the
            # common polling path (also covers an unchanged partial last line).
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
                # Backups/copies change inodes, but a matching head and checkpoint
                # boundary retain the same logical prefix and can resume safely.
                matches = [path for path in paths if _matches_content(path, source)]
            if len(matches) == 1:
                paths = paths[paths.index(matches[0]):]
            else:
                reason = "log_history_changed"

    if reason and not paths:
        raise MissionViewReplayError("Mission View cursor refers to missing event history")

    if cursor is None or reason:
        previous = view
        has_history = any(path.stat().st_size for path in paths)
        view = empty_mission_view() if has_history or reason else previous
        if has_history and not reason and previous.get("bootstrapped"):
            view["_projection_legacy"] = {
                "mission": dict(previous.get("mission") or {}),
                "stage": dict(previous.get("stage") or {"id": "", "label": ""}),
            }
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
                    # Persist discard progress so even interrupted recovery has
                    # a bounded next pass and counts the row only once.
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
                    cursor["last_event_id"] = "oversized:" + hashlib.sha256(raw).hexdigest()
                    skipped += 1
                    oversized += 1
                    continue
                if not raw.endswith(b"\n") and path == current:
                    waiting = True
                    break
                consumed += len(raw)
                offset = handle.tell()
                cursor["last_event_id"] = hashlib.sha256(raw).hexdigest()
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
                # This is the exact ownership filter formerly used by the
                # snapshot's review-tail repair. Moving it into canonical replay
                # keeps a late prior-task review from overwriting the new owner.
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
    if caught_up:
        _preserve_legacy_details(view)
    _write_unlocked(root, view)
    return view


def load_reconciled_view(root: Path, *, force_logged: bool = False) -> dict[str, Any]:
    with events_locked(root), _locked(root):
        return reconcile_unlocked(root, _read_unlocked(root), force_logged=force_logged)
