"""Observable status of the reflection after each mission.

A SELF turn keeps what it learned in the same turn; only mission reflection
runs separately, and this store shows its progress and outcome.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.daemon_lock import is_pid_running


@contextmanager
def _database(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "answer-learning.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, sid TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT '{}'
        )""")
        if not db.execute("PRAGMA user_version").fetchone()[0]:
            # Once: drop the rows the retired per-answer queue left behind.
            db.executescript("DELETE FROM jobs WHERE id NOT LIKE 'mission-%'; PRAGMA user_version = 1;")
        with db:
            yield db
    finally:
        db.close()


def learning_status(root: Path, sid: str) -> dict[str, Any]:
    """A bounded projection with no prompt, answer, credential or filesystem path."""
    if not (root / "answer-learning.sqlite3").exists():
        return {"jobs": [], "pending": 0, "revision": 0}
    with _database(root) as db:
        rows = db.execute(
            "SELECT id, status, created, updated, attempts, outcome, payload FROM jobs "
            "WHERE sid=? ORDER BY created DESC LIMIT 20", (sid,),
        ).fetchall()
        pending = db.execute(
            "SELECT count(*) FROM jobs WHERE sid=? AND status='running'", (sid,),
        ).fetchone()[0]
        revision = db.execute("SELECT max(updated) FROM jobs WHERE sid=?", (sid,)).fetchone()[0]
    jobs = []
    for row in rows:
        job = dict(row)
        payload = json.loads(job.pop("payload"))
        job["outcome"] = json.loads(job["outcome"])
        if job["status"] == "running":
            # Never probe with os.kill(pid, 0): on Windows CPython maps every
            # non-console signal to TerminateProcess, so this read-only status
            # poll used to kill the running daemon that owns the job.
            try:
                owner_alive = is_pid_running(int(payload["owner_pid"]))
            except (KeyError, TypeError, ValueError):
                owner_alive = False
            if not owner_alive:
                job["status"] = "failed"
                pending = max(0, pending - 1)
        jobs.append(job)
    return {"jobs": jobs, "pending": pending, "revision": revision or 0}


# Calls the host refused before they started: nothing failed, learning simply waits.
_PAUSE_KINDS = frozenset({"cost_unreconciled", "budget_exhausted", "provider_cooldown", "operator_pause"})


def _pause_reason(result: dict) -> str:
    kind = str(result.get("stop_kind") or "")
    return kind if kind in _PAUSE_KINDS else ""


def _finish(root: Path, job_id: str, result: dict, entries: list[dict]) -> None:
    counts = {"knowledge": 0, "skills": 0, "preferences": 0}
    items = []
    for event in entries:
        channel = "skills" if event.get("page_kind") == "skill" else (
            "preferences" if event.get("scope") == "private" else "knowledge")
        counts[channel] += 1
        items.append({"channel": channel, "title": event.get("title", ""),
                      "scope": event.get("scope", ""), "path": event.get("path", "")})
    paused = _pause_reason(result) if result.get("failure") else ""
    outcome: dict[str, Any] = {"counts": counts, "items": items}
    if paused:
        # Denied before start by host policy: record why, not a failure.
        status, outcome["reason"], outcome["detail"] = "skipped", "paused", paused
    elif result.get("failure"):
        status, outcome["reason"] = "failed", "failed"
    elif result.get("skipped") and not any(counts.values()):
        # Nothing to learn from (learning off, no backend): stay quiet.
        status, outcome["reason"] = "skipped", "not_needed"
    elif any(counts.values()):
        status, outcome["reason"] = "completed", "saved"
    else:
        status, outcome["reason"] = "unchanged", "no_new_learning"
    with _database(root) as db:
        db.execute("UPDATE jobs SET status=?, updated=?, outcome=? WHERE id=?",
                   (status, time.time(), json.dumps(outcome, ensure_ascii=False), job_id))


def observe_mission(root: Path, sid: str, mission_id: str, run, emit) -> dict:
    """Record a mission reflection's progress and outcome for the status surface."""
    job_id = f"mission-{sid}:{mission_id}"
    entries = []

    def capture(event):
        if event.get("type") == "knowledge.learned":
            entries.append(event)
        if emit:
            emit(event)

    with _database(root) as db:
        now = time.time()
        db.execute(
            "INSERT OR REPLACE INTO jobs (id,sid,payload,status,created,updated,attempts) VALUES (?,?,?,'running',?,?,1)",
            (job_id, sid, json.dumps({"owner_pid": os.getpid()}), now, now),
        )
    try:
        result = run(capture)
    except Exception:
        _finish(root, job_id, {"failure": "reflection failed"}, entries)
        raise
    _finish(root, job_id, result, entries)
    return result
