"""On-demand history pages and an evidence index outside research records."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from ..core.json_codec import loads_finite_json
from ..core.jsonl_reader import event_log_read_lock, read_jsonl_batch
from ..life.memory import _jsonl_history_paths
from .map_view import digest, normalize_events, turn_records, with_revisions

PAGE_BYTES = 1024 * 1024
PAGE_EVENTS = 500
# Bump when the projection learns to derive new records from old rows, so an
# index built by an earlier version is rebuilt instead of trusted.
HISTORY_VERSION = 9


def _finite_record(value: str | bytes) -> dict | None:
    """Skip invalid log/cache records without changing their audit source."""
    try:
        record = loads_finite_json(value)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def history_path(root: Path, life_dir: Path) -> Path:
    return root / "map-history-cache" / (digest(str(life_dir.resolve())) + ".sqlite")


def history_info(value: dict, life_dir: Path) -> dict:
    event_path = life_dir / "events.jsonl"
    with event_log_read_lock(event_path):
        size = sum(path.stat().st_size for path in _jsonl_history_paths(event_path))
    tasks = value["tasks"]
    active = [t for t in tasks if t.get("status") in ("running", "in_progress", "claimed")]
    pending = [t for t in tasks if t.get("status") == "pending"]
    anchor = next(iter(active or pending or tasks[-1:]), {})
    return {
        "task_count": len(tasks), "event_bytes": size,
        "requires_choice": len(tasks) >= 40 or size >= 8 * 1024 * 1024,
        "current_task_id": anchor.get("id"),
        "current_task_ts": anchor.get("ts", 0),
        "current_event_ts": anchor.get("started_ts") or anchor.get("ts", 0),
    }


def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, id TEXT UNIQUE, body TEXT)")
    return db


@dataclass(slots=True)
class _HistoryRead:
    state: dict
    batches: list[list[dict]]
    total_bytes: int
    more: bool
    reset: bool
    waiting: bool


def _read_history_batches(life_dir: Path, state: dict, task_ids: list[str]) -> _HistoryRead:
    """Hold the log lock for bounded IO, then normalize/index outside it."""
    event_path = life_dir / "events.jsonl"
    with event_log_read_lock(event_path):
        files = [(path, path.stat()) for path in _jsonl_history_paths(event_path)]
        size = sum(stat.st_size for _, stat in files)
        known_ids, previous_ids = set(task_ids), set(state.get("task_ids", []))
        saved_files = state.get("files", [])
        reset = (
            "files" not in state or state.get("version") != HISTORY_VERSION
            or bool(previous_ids - known_ids)
            or bool((known_ids - previous_ids) & set(state.get("omitted_owners", [])))
            or len(saved_files) > len(files)
        )
        # Rename preserves order and identity. Immutable archived generations
        # need only stat comparison; do not reopen every old anchor each page.
        for saved, (path, stat) in zip(saved_files, files):
            offset = saved["offset"]
            if saved["identity"] != [stat.st_dev, stat.st_ino] or stat.st_size < offset:
                reset = True
                break
            if saved.get("observed_size") == stat.st_size and saved.get("mtime_ns") == stat.st_mtime_ns:
                continue
            if offset:
                with path.open("rb") as stream:
                    stream.seek(max(0, offset - 128))
                    if digest(stream.read(min(offset, 128)).hex()) != saved.get("anchor"):
                        reset = True
                        break
        if not state or reset:
            state = {"files": [], "task_ids": task_ids, "active": [], "omitted_owners": [],
                     "version": HISTORY_VERSION,
                     "epoch": digest([str(life_dir), time.time_ns()])}
        remaining = PAGE_BYTES
        batches: list[list[dict]] = []
        more = waiting = False
        for index, (path, stat) in enumerate(files):
            if index == len(state["files"]):
                state["files"].append({"identity": [stat.st_dev, stat.st_ino], "offset": 0})
            saved = state["files"][index]
            offset = saved["offset"]
            if stat.st_size <= offset and not saved.get("discarding"):
                continue
            if (
                path == event_path and saved.get("waiting_for_line")
                and saved.get("observed_size") == stat.st_size
                and saved.get("mtime_ns") == stat.st_mtime_ns
            ):
                waiting = True
                continue
            if remaining <= 0:
                more = True
                break
            with path.open("rb") as stream:
                batch = read_jsonl_batch(stream, offset, byte_limit=remaining,
                                         discarding=bool(saved.get("discarding")),
                                         sealed=path != event_path)
                stream.seek(max(0, batch.offset - 128))
                anchor = digest(stream.read(min(batch.offset, 128)).hex())
            remaining -= batch.bytes_read
            saved.update(offset=batch.offset, anchor=anchor, discarding=batch.discarding,
                         observed_size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                         waiting_for_line=batch.waiting_for_line)
            state["skipped_rows"] = state.get("skipped_rows", 0) + batch.skipped_rows
            state["oversized_rows"] = state.get("oversized_rows", 0) + batch.oversized_rows
            if batch.rows:
                batches.append(batch.rows)
            more, waiting = batch.more, waiting or batch.waiting_for_line
            if more:
                break
        return _HistoryRead(state, batches, size, more, reset, waiting)


def history_page(root: Path, life_dir: Path, value: dict, after: str | None) -> dict:
    db = _connect(history_path(root, life_dir))
    try:
        with db:
            # Serialize index writers across API workers as well as browser tabs.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM metadata WHERE key = 'state'").fetchone()
            state = (_finite_record(row[0]) or {}) if row else {}
            task_ids = sorted(t["id"] for t in value["tasks"])
            known_ids = set(task_ids)
            read = _read_history_batches(life_dir, state, task_ids)
            state = read.state
            if read.reset:
                db.execute("DELETE FROM events")
            active = set(state["active"])
            omitted = set(state.get("omitted_owners", []))
            # Work segments still open and chat turns awaiting their reply
            # carry over between pages, like the active-mission window does.
            segments = state.get("segments") or {}
            turn_asks = state.get("turn_asks") or {}
            for rows in read.batches:
                owners = known_ids | active | {
                    str(row.get("item_id") or row.get("mission_id") or "") for row in rows
                }
                normalized = normalize_events(rows, owners - {""}, active, segments)
                omitted.update(e["item_id"] for e in normalized if e["item_id"] not in known_ids)
                events = [e for e in normalized if e["item_id"] in known_ids]
                # Turn cards are derived from these very events, so their
                # records need no owner in the task list to be kept.
                events.extend(
                    event
                    for turn in turn_records(rows, {}, turn_asks).values()
                    for event in turn["events"]
                )
                # A rewritten event (a step retired as superseded, streamed
                # text finalized) must reach readers whose cursor already
                # passed its seq: delete + insert under an explicitly
                # monotonic counter re-emits it after every issued cursor.
                # (Bare rowids reuse max+1 after a delete, which can land
                # exactly ON a handed-out cursor and stay invisible.)
                # Clients merge by event id, so re-delivery is the update
                # path, not a duplicate.
                fresh = list({e["id"]: e for e in events}.values())
                counter = int(state.get("seq") or 0)
                if not counter:
                    row = db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
                    counter = int(row[0])
                db.executemany(
                    "DELETE FROM events WHERE id = ?",
                    [(e["id"],) for e in fresh],
                )
                db.executemany(
                    "INSERT INTO events (seq, id, body) VALUES (?, ?, ?)",
                    [
                        (counter + index + 1, e["id"], json.dumps(e, ensure_ascii=False, allow_nan=False))
                        for index, e in enumerate(fresh)
                    ],
                )
                state["seq"] = counter + len(fresh)
            state.update(active=sorted(active), task_ids=task_ids, omitted_owners=sorted(omitted),
                         segments=segments, turn_asks=turn_asks,
                         offset=sum(saved["offset"] for saved in state["files"]))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('state', ?)", (json.dumps(state, allow_nan=False),))
            epoch, _, number = (after or "").partition(":")
            valid = epoch == state["epoch"] and number.isdigit()
            start = int(number) if valid else 0
            rows = db.execute("SELECT seq, body FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                              (min(start, 2**63 - 1), PAGE_EVENTS + 1)).fetchall()
            events = [event for row in rows[:PAGE_EVENTS] if (event := _finite_record(row[1])) is not None]
            last = rows[min(len(rows), PAGE_EVENTS) - 1][0] if rows else start
            return with_revisions({
                **value, "events": events, "incremental": valid,
                "reset_history": bool(after) and not valid,
                "history_cursor": f"{state['epoch']}:{last}",
                "history_loading": read.more or len(rows) > PAGE_EVENTS,
                "history_progress": {"loaded_bytes": state["offset"], "total_bytes": read.total_bytes,
                                     "waiting_for_line": read.waiting,
                                     "skipped_rows": state.get("skipped_rows", 0),
                                     "oversized_rows": state.get("oversized_rows", 0)},
            })
    finally:
        db.close()


def indexed_evidence(root: Path, life_dir: Path, ids: list[str]) -> list[dict]:
    path = history_path(root, life_dir)
    if not path.is_file() or not ids:
        return []
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in ids)
        # Summary requests can arrive before history_page migrates an old
        # index. Keep usable cached evidence and omit malformed rows read-only.
        return [event for row in db.execute(
            f"SELECT body FROM events WHERE id IN ({placeholders})", ids,
        ) if (event := _finite_record(row[0])) is not None]
    finally:
        db.close()
