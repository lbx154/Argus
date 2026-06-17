"""Team manifest: who is in the team, what they own, and their liveness.

Persisted atomically under an exclusive lock. The roster plus each
teammate's living doc (``TEAMMATE_STATUS.md`` in its worktree) is the
restart-resume anchor: on daemon restart the lead reads the roster, finds
half-run members, and re-spawns them from their doc.
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


def mark(root: Path, member_id: str, *, status: str, now: float) -> None:
    with _store.locked(_lock(root)):
        doc = load(root)
        for m in doc.get("members", []):
            if m.get("id") == member_id:
                m["status"] = status
                m["heartbeat_ts"] = now
        _store.atomic_write_json(_path(root), doc)


def set_state(root: Path, state: str) -> None:
    with _store.locked(_lock(root)):
        doc = load(root)
        doc["state"] = state
        _store.atomic_write_json(_path(root), doc)


def stale_members(root: Path, *, ttl: float, now: float) -> list[str]:
    """Members marked running/idle whose heartbeat aged past ``ttl``."""
    out: list[str] = []
    for m in members(root):
        if m.get("status") in ("running", "idle") and now - float(m.get("heartbeat_ts", 0)) > ttl:
            out.append(m["id"])
    return out
