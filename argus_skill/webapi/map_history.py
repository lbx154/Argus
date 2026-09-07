"""On-demand history pages and an evidence index outside research records."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .map_view import digest, normalize_events, with_revisions

PAGE_BYTES = 1024 * 1024
PAGE_EVENTS = 500


def history_path(root: Path, life_dir: Path) -> Path:
    return root / "map-history-cache" / (digest(str(life_dir.resolve())) + ".sqlite")


def history_info(value: dict, life_dir: Path) -> dict:
    path = life_dir / "events.jsonl"
    size = path.stat().st_size if path.is_file() else 0
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


def history_page(root: Path, life_dir: Path, value: dict, after: str | None) -> dict:
    path = life_dir / "events.jsonl"
    db = _connect(history_path(root, life_dir))
    try:
        with db:
            # Serialize index writers across API workers as well as browser tabs.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM metadata WHERE key = 'state'").fetchone()
            state = json.loads(row[0]) if row else {}
            stat = path.stat() if path.is_file() else None
            identity = [stat.st_dev, stat.st_ino] if stat else None
            size = stat.st_size if stat else 0
            offset = state.get("offset", 0)
            reset = state.get("identity") != identity or size < offset
            if stat:
                with path.open("rb") as stream:
                    stream.seek(max(0, offset - 128))
                    if offset and digest(stream.read(min(offset, 128)).hex()) != state.get("anchor"):
                        reset = True
            if not state or reset:
                db.execute("DELETE FROM events")
                state = {"identity": identity, "offset": 0, "active": [],
                         "epoch": digest([str(life_dir), identity, time.time_ns()])}
                offset = 0
            more_bytes = False
            if stat and size > offset:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    payload = stream.read(PAGE_BYTES)
                    if payload and not payload.endswith(b"\n"):
                        payload += stream.readline()
                    consumed = offset
                    rows = []
                    for line in payload.splitlines(keepends=True):
                        if not line.endswith(b"\n"):
                            continue
                        consumed += len(line)
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict):
                            rows.append(row)
                    active = set(state["active"])
                    events = normalize_events(rows, {t["id"] for t in value["tasks"]}, active)
                    db.executemany(
                        "INSERT INTO events (id, body) VALUES (?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                        [(e["id"], json.dumps(e, ensure_ascii=False)) for e in events],
                    )
                    more_bytes = stream.tell() < size
                    stream.seek(max(0, consumed - 128))
                    state.update(offset=consumed, active=sorted(active),
                                 anchor=digest(stream.read(min(consumed, 128)).hex()))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('state', ?)", (json.dumps(state),))
            epoch, _, number = (after or "").partition(":")
            valid = epoch == state["epoch"] and number.isdigit()
            start = int(number) if valid else 0
            rows = db.execute("SELECT seq, body FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                              (min(start, 2**63 - 1), PAGE_EVENTS + 1)).fetchall()
            events = [json.loads(row[1]) for row in rows[:PAGE_EVENTS]]
            last = rows[min(len(rows), PAGE_EVENTS) - 1][0] if rows else start
            return with_revisions({
                **value, "events": events, "incremental": valid,
                "reset_history": bool(after) and not valid,
                "history_cursor": f"{state['epoch']}:{last}",
                "history_loading": more_bytes or len(rows) > PAGE_EVENTS,
                "history_progress": {"loaded_bytes": state["offset"], "total_bytes": size},
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
        return [json.loads(row[0]) for row in db.execute(
            f"SELECT body FROM events WHERE id IN ({placeholders})", ids,
        )]
    finally:
        db.close()
