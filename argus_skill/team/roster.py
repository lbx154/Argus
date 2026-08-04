"""Durable team manifest and teammate-process registry.

The resident Curator records each spawned process here.  After an unclean
daemon restart it can adopt a still-running process only after verifying the
recorded PID's command line; task state and result shards remain authoritative
for work completion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _store


def _path(root: Path) -> Path:
    return Path(root) / "roster.json"


def _lock(root: Path) -> Path:
    return Path(root) / ".roster.lock"


def create(root: Path, *, team_id: str, mission: str, lead: str, now: float) -> None:
    with _store.locked(_lock(root)):
        existing = load(root)
        if existing.get("team_id"):
            if existing.get("team_id") != team_id:
                raise ValueError(
                    f"team root {root} already belongs to {existing.get('team_id')!r}, "
                    f"not {team_id!r}"
                )
            existing.setdefault("mission_objective", mission)
            existing.setdefault("lead", lead)
            existing.setdefault("created_ts", now)
            existing.setdefault("state", "forming")
            existing.setdefault("members", [])
            _store.atomic_write_json(_path(root), existing)
            return
        _store.atomic_write_json(_path(root), {
            "team_id": team_id,
            "mission_objective": mission,
            "lead": lead,
            "created_ts": now,
            "state": "forming",
            "members": [],
        })


def load(root: Path) -> dict[str, Any]:
    return _store.read_json(_path(root), default={}) or {}


def members(root: Path) -> list[dict[str, Any]]:
    return list(load(root).get("members", []))


def add_member(root: Path, member: dict[str, Any]) -> None:
    """Add or replace a member record (keyed by ``id``)."""
    with _store.locked(_lock(root)):
        doc = load(root)
        existing = [m for m in doc.get("members", []) if m.get("id") != member.get("id")]
        existing.append(member)
        doc["members"] = existing
        _store.atomic_write_json(_path(root), doc)


def set_member_status(root: Path, member_id: str, status: str) -> None:
    """Update one member's process status without dropping its PID/task metadata."""
    with _store.locked(_lock(root)):
        doc = load(root)
        changed = False
        for member in doc.get("members", []):
            if member.get("id") == member_id:
                member["status"] = status
                changed = True
                break
        if changed:
            _store.atomic_write_json(_path(root), doc)


def _member_seq_from_id(value: object, *, prefix: str) -> int:
    text = str(value or "")
    if text.startswith(prefix) and text[len(prefix):].isdigit():
        return int(text[len(prefix):])
    return 0


def _max_task_owner_seq(root: Path, *, prefix: str) -> int:
    tasks_dir = Path(root) / "tasks"
    if not tasks_dir.exists():
        return 0
    max_seq = 0
    for path in tasks_dir.glob("*.json"):
        task = _store.read_json(path, default=None)
        if isinstance(task, dict):
            max_seq = max(max_seq, _member_seq_from_id(task.get("owner"), prefix=prefix))
    return max_seq


def next_member_id(root: Path, *, prefix: str = "w") -> str:
    """Atomically allocate a unique, monotonic member id like ``w1``, ``w2``.

    The Curator calls this for every teammate it starts so ids never collide
    inside one campaign. Works even if ``create()`` was never called.

    Long-running teams can be restocked and repaired while tasks still carry
    owners from roster generations that were later compacted or accidentally
    reset. Derive the next sequence from every durable owner source, not just
    the stored counter, so a restarted Curator never reuses ids such as
    ``w1600``.
    """
    with _store.locked(_lock(root)):
        doc = load(root)
        seq_floor = int(doc.get("member_seq", 0) or 0)
        for member in doc.get("members", []):
            seq_floor = max(seq_floor, _member_seq_from_id(member.get("id"), prefix=prefix))
        seq_floor = max(seq_floor, _max_task_owner_seq(root, prefix=prefix))
        seq = seq_floor + 1
        doc["member_seq"] = seq
        _store.atomic_write_json(_path(root), doc)
        return f"{prefix}{seq}"


def set_state(root: Path, state: str) -> None:
    with _store.locked(_lock(root)):
        doc = load(root)
        doc["state"] = state
        _store.atomic_write_json(_path(root), doc)
