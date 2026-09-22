"""Durable, serial learning from delivered answers, independent of reply latency.

The queue is per operator home: projects share knowledge and a private profile,
so their read/modify/write learning passes must not race with one another.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.file_lock import exclusive_file_lock

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_THREADS: dict[Path, threading.Thread] = {}
_BACKENDS: dict[tuple[Path, str], Any] = {}


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
            "SELECT count(*) FROM jobs WHERE sid=? AND status IN ('queued','running')", (sid,),
        ).fetchone()[0]
        revision = db.execute("SELECT max(updated) FROM jobs WHERE sid=?", (sid,)).fetchone()[0]
    jobs = []
    for row in rows:
        job = dict(row)
        payload = json.loads(job.pop("payload"))
        job["outcome"] = json.loads(job["outcome"])
        job["retryable"] = not job["id"].startswith("mission-")
        if not job["retryable"] and job["status"] == "running":
            try:
                os.kill(int(payload["owner_pid"]), 0)
            except (ProcessLookupError, KeyError, ValueError):
                job["status"] = "failed"
                pending = max(0, pending - 1)
            except PermissionError:
                pass
        jobs.append(job)
    return {"jobs": jobs, "pending": pending, "revision": revision or 0}


def enqueue_answer(
    *, root: Path, sid: str, operator_text: str, reply: str, vertical: str = "",
    evidence: str = "", turn_id: str = "", backend: Any = None,
) -> threading.Thread:
    root = root.resolve()
    identity = turn_id or f"{operator_text}\0{reply}"
    job_id = hashlib.sha256(f"{sid}\0{identity}".encode()).hexdigest()[:32]
    payload = {"operator_text": operator_text, "reply": reply,
               "vertical": vertical, "evidence": evidence}
    with _LOCK:
        with _database(root) as db:
            now = time.time()
            db.execute(
                "INSERT OR IGNORE INTO jobs (id,sid,payload,status,created,updated) VALUES (?,?,?,'queued',?,?)",
                (job_id, sid, json.dumps(payload, ensure_ascii=False), now, now),
            )
        if backend is not None:
            _BACKENDS[root, sid] = backend
        return resume_learning(root)


def retry_learning(root: Path, sid: str, job_id: str) -> bool:
    root = root.resolve()
    with _LOCK:
        with _database(root) as db:
            changed = db.execute(
                "UPDATE jobs SET status='queued', updated=? WHERE id=? AND sid=? AND status='failed' AND id NOT LIKE 'mission-%'",
                (time.time(), job_id, sid),
            ).rowcount
        if changed:
            resume_learning(root)
        return bool(changed)


def resume_learning(root: Path) -> threading.Thread:
    root = root.resolve()
    with _LOCK:
        active = _THREADS.get(root)
        if active is not None:
            return active
        thread = threading.Thread(target=_drain, args=(root,), name="argus-answer-learning", daemon=True)
        _THREADS[root] = thread
        thread.start()
        return thread


def _backend(root: Path, sid: str) -> Any:
    cached = _BACKENDS.get((root, sid))
    if cached is not None:
        return cached
    # Reconstruct the configured transport after restart, without sending a
    # new user turn or running the task again.
    from ..life.memory import MemoryBundle
    from ..manager.front_door import _ensure_manager_runner
    from ..webapi.manager_state import _chat_state_for, _lock_for

    with _lock_for(sid):
        state = _chat_state_for(sid, manager_activity=False)
        state.update(session_id=sid, global_root=str(root))
        runner = _ensure_manager_runner(state, MemoryBundle.for_cwd(fingerprint=sid, global_root=root))
        backend = getattr(runner, "_backend", None)
    if backend is None:
        raise RuntimeError("Learning backend is unavailable")
    return backend


def _finish(root: Path, job: dict, result: dict, entries: list[dict]) -> None:
    counts = {"knowledge": 0, "skills": 0, "preferences": 0}
    items = []
    for event in entries:
        channel = "skills" if event.get("page_kind") == "skill" else (
            "preferences" if event.get("scope") == "private" else "knowledge")
        counts[channel] += 1
        items.append({"channel": channel, "title": event.get("title", ""),
                      "scope": event.get("scope", ""), "path": event.get("path", "")})
    failed = bool(result.get("failure") or result.get("skipped"))
    status = "failed" if failed else "completed" if any(counts.values()) else "unchanged"
    outcome = {"counts": counts, "items": items, "reason": "failed" if failed else (
        "saved" if any(counts.values()) else "no_new_learning")}
    with _database(root) as db:
        db.execute("UPDATE jobs SET status=?, updated=?, outcome=? WHERE id=?",
                   (status, time.time(), json.dumps(outcome, ensure_ascii=False), job["id"]))


def _process(root: Path, job: dict) -> None:
    from .event_log import JsonlEventSink
    from .reflection import reflect_after_answer

    entries: list[dict] = []
    life_dir = root / "projects" / job["sid"]
    sink = JsonlEventSink(None, life_dir=life_dir)

    def emit(event: dict) -> None:
        if event.get("type") == "knowledge.learned":
            entries.append(event)
        sink.append(event)

    try:
        if not life_dir.is_dir():
            raise RuntimeError("Project was removed before learning")
        result = reflect_after_answer(
            runner_backend=_backend(root, job["sid"]), global_root=root, life_dir=life_dir,
            project_id=job["sid"], emit=emit, **json.loads(job["payload"]),
        )
    except Exception:  # noqa: BLE001 - learning cannot take down the reply or queue
        log.exception("answer learning job %s failed", job["id"])
        result = {"failure": "learning failed"}
    _finish(root, job, result, entries)


def observe_mission(root: Path, sid: str, mission_id: str, run, emit) -> dict:
    """Expose the existing mission reflection through the same status surface."""
    job_id = "mission-" + hashlib.sha256(f"{sid}\0{mission_id}".encode()).hexdigest()[:32]
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
        _finish(root, {"id": job_id}, {"failure": "reflection failed"}, entries)
        raise
    _finish(root, {"id": job_id}, result, entries)
    return result


def _drain(root: Path) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / ".answer-learning.lock").open("a+") as handle:
            with exclusive_file_lock(handle, timeout_seconds=600):
                # Acquiring the process lease proves any earlier owner is gone.
                # A killed worker's current answer stays in the durable queue.
                with _database(root) as db:
                    db.execute("UPDATE jobs SET status='queued', updated=? WHERE status='running' AND id NOT LIKE 'mission-%'", (time.time(),))
                while True:
                    with _LOCK:
                        with _database(root) as db:
                            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                            if row is None:
                                _THREADS.pop(root, None)
                                return
                            db.execute("UPDATE jobs SET status='running', updated=?, attempts=attempts+1 WHERE id=?",
                                       (time.time(), row["id"]))
                    _process(root, dict(row))
    except Exception:
        log.exception("answer learning queue stopped; pending work is retained")
    finally:
        with _LOCK:
            if _THREADS.get(root) is threading.current_thread():
                _THREADS.pop(root, None)
            for key in list(_BACKENDS):
                if key[0] == root and root not in _THREADS:
                    _BACKENDS.pop(key, None)
