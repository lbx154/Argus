"""What a mission is waiting on, read once for the project snapshot.

When the runtime forms an idea portfolio, the lead Engineer waits for the
team instead of spending model rounds (``engineer.round_waits``): first inside
its round, then by parking the mission as ``paused_external_work`` so the
daemon resumes it when the board settles. The snapshot used to say only
"running" or "paused". The sidebar, the map card, the task internals and the
conversation header each drew their own conclusion from that and, on the
2026-09-30 trial, four of them contradicted one another on one screen. This
module reads each team board the project waits on and reports how far it has
come and which missions wait on it, so every surface can say the same thing.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType

WAITING_STATUSES = frozenset({"paused_external_work", "running"})
TEAM_WORK_PREFIX = "team:"
_IN_FLIGHT = frozenset({"claimed", "running"})
_ATTENTION = frozenset({"failed", "blocked"})
_MAX_TEAMS = 16
_MAX_TASKS = 512
# Routes are read before their reviews and the selection that closes them.
_ROLE_ORDER = {"idea-route": 0, "idea-review": 1, "idea-selector": 2}


def _number(value: Any) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) and result >= 0 else 0.0


def waited_work_ids(items: list[dict], recent_events: list[dict]) -> dict[str, list[str]]:
    """Which mission waits on which work: the parked wait on the backlog item,
    or the in-round wait the journal tail records for a running mission."""
    latest: dict[str, tuple[float, str, str]] = {}
    for event in recent_events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        if kind not in (
            EventType.ROUND_EXTERNAL_WORK_WAIT_STARTED,
            EventType.ROUND_EXTERNAL_WORK_WAIT_COMPLETED,
        ):
            continue
        item_id = str(event.get("item_id") or "")
        ts = _number(event.get("ts"))
        if item_id and ts >= latest.get(item_id, (0.0, "", ""))[0]:
            latest[item_id] = (ts, kind, str(event.get("work_id") or ""))
    waits: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        status = str(item.get("status") or "")
        if not item_id or status not in WAITING_STATUSES:
            continue
        work_id = ""
        if status == "paused_external_work":
            outcome = item.get("outcome")
            wait = outcome.get("external_wait") if isinstance(outcome, dict) else None
            if isinstance(wait, dict):
                work_id = str(wait.get("work_id") or "")
        else:
            ts, kind, recorded = latest.get(item_id, (0.0, "", ""))
            if kind == EventType.ROUND_EXTERNAL_WORK_WAIT_STARTED and ts >= _number(item.get("started_ts")):
                work_id = recorded
        if work_id:
            waits.setdefault(work_id, []).append(item_id)
    return waits


def _team_board(workdir: Path, team_id: str, marker: dict | None) -> Path | None:
    raw = str((marker or {}).get("team_root") or "") or str(workdir / ".argus" / "teams" / team_id)
    try:
        teams = (workdir / ".argus" / "teams").resolve()
        board = Path(raw).expanduser().resolve(strict=True)
        if board.parent != teams or not board.is_dir():
            return None
    except (OSError, ValueError):
        return None
    return board


def team_progress(board: Path, marker: dict | None = None, *, now: float | None = None) -> dict[str, Any] | None:
    """One team board as counts a person can read, by kind of subtask."""
    from ..team import task_board

    tasks = task_board.snapshot(board)[:_MAX_TASKS]
    if not tasks:
        return None
    observed_at = time.time() if now is None else float(now)
    counts = {"total": 0, "done": 0, "running": 0, "pending": 0, "attention": 0}
    parts: dict[str, dict[str, int]] = {}
    last_progress = _number((marker or {}).get("created_ts"))
    for task in tasks:
        state = str(task.get("state") or "")
        bucket = (
            "done" if state == "done"
            else "running" if state in _IN_FLIGHT
            else "attention" if state in _ATTENTION
            else "pending"
        )
        role = str(task.get("role") or "")
        part = parts.setdefault(role, {"total": 0, "done": 0, "running": 0, "attention": 0})
        for scope in (counts, part):
            scope["total"] += 1
            if bucket in scope:
                scope[bucket] += 1
        for field in ("heartbeat_ts", "claim_ts", "finished_ts"):
            last_progress = max(last_progress, min(_number(task.get(field)), observed_at))
    state = (
        "done" if counts["done"] == counts["total"]
        else "attention" if counts["attention"] and not counts["running"]
        else "running"
    )
    return {
        "team_id": board.name,
        "work_id": TEAM_WORK_PREFIX + board.name,
        "owner": str((marker or {}).get("owner") or ""),
        "state": state,
        **counts,
        "parts": [
            {"role": role, **part}
            for role, part in sorted(parts.items(), key=lambda item: (_ROLE_ORDER.get(item[0], 9), item[0]))
        ],
        "started_ts": _number((marker or {}).get("created_ts")) or None,
        "last_progress_ts": last_progress or None,
    }


def background_work(
    workdir: Path | str | None,
    items: list[dict],
    recent_events: list[dict],
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Every team the project's open missions wait on, with its progress.

    Empty unless a mission is running or parked on background work, so a
    finished project costs nothing. Registered teams are listed even when no
    mission is known to wait on them yet: the page keeps a longer journal than
    the snapshot and may know the wait the snapshot tail no longer shows.
    """
    if not workdir:
        return []
    if not any(
        isinstance(item, dict) and str(item.get("status") or "") in WAITING_STATUSES
        for item in items
    ):
        return []
    from ..team import registry

    root = Path(str(workdir)).expanduser()
    waits = waited_work_ids(items, recent_events)
    markers: dict[str, dict] = {}
    try:
        for marker in registry.list_markers(root):
            team_id = str(marker.get("team_id") or "").strip()
            if team_id:
                markers[team_id] = marker
    except OSError:
        markers = {}
    wanted: list[str] = list(markers)
    for work_id in waits:
        if work_id.startswith(TEAM_WORK_PREFIX) and work_id[len(TEAM_WORK_PREFIX):] not in wanted:
            wanted.append(work_id[len(TEAM_WORK_PREFIX):])
    entries: list[dict[str, Any]] = []
    for team_id in wanted[:_MAX_TEAMS]:
        board = _team_board(root, team_id, markers.get(team_id))
        if board is None:
            continue
        try:
            progress = team_progress(board, markers.get(team_id), now=now)
        except (OSError, ValueError, TypeError):
            continue
        if progress is None:
            continue
        progress["waited_by"] = list(waits.get(progress["work_id"], ()))
        entries.append(progress)
    entries.sort(key=lambda entry: (not entry["waited_by"], entry.get("started_ts") or 0.0, entry["team_id"]))
    return entries


__all__ = ["WAITING_STATUSES", "background_work", "team_progress", "waited_work_ids"]
