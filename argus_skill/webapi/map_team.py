"""Read-only Team taskboard observations nested under their recorded mission."""
from __future__ import annotations

import json
import math
from itertools import islice
from pathlib import Path

from ..core.session import read_session_meta, resolve_session_workdir
from .map_view import digest, text

FORMATION_BYTES = 8 * 1024 * 1024
MAX_TEAM_BINDINGS = 32
MAX_TEAM_TASKS = 512
MAX_TASK_BYTES = 256 * 1024


def _stamp(path: Path):
    try:
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _number(value) -> float:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) and result >= 0 else 0.0


def remember_formations(rows: list[dict], task_ids: set[str], bindings: dict) -> bool:
    for row in rows:
        if row.get("type") != "idea.portfolio.formed":
            continue
        owner = str(row.get("item_id") or "")
        path = str(row.get("team_root") or "")
        if owner not in task_ids or not path:
            continue
        old = bindings.get(path)
        # Repeated preparation of one portfolio does not move every child on
        # the map. A newly recorded parent gets a separate observation identity.
        if old and old["item_id"] == owner:
            continue
        bindings[path] = {"item_id": owner, "ts": _number(row.get("ts"))}
    truncated = len(bindings) > MAX_TEAM_BINDINGS
    while len(bindings) > MAX_TEAM_BINDINGS:
        del bindings[next(iter(bindings))]
    return truncated


def previous_formations(life_dir: Path) -> list[dict]:
    """Recover bounded ownership evidence when the current log has rotated."""
    try:
        with (life_dir / "events.jsonl.1").open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - FORMATION_BYTES)
            stream.seek(start)
            if start:
                stream.readline()
            raw = stream.read(FORMATION_BYTES)
    except OSError:
        return []
    rows = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n") or b'"idea.portfolio.formed"' not in line:
            continue
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _sources(sid: str, root: Path, life_dir: Path, bindings: dict):
    if not bindings:
        return (), [], False
    try:
        from ..core.campaign_workdir import active_campaign_workdir

        workdir = resolve_session_workdir(read_session_meta(root, sid), state_dir=life_dir)
        workdir = active_campaign_workdir(life_dir, workdir) or workdir
        teams = (workdir / ".argus" / "teams").resolve()
        if not teams.is_relative_to(workdir):
            return (str(workdir), "outside-workdir"), [], False
    except (OSError, ValueError):
        return ("unavailable-workdir",), [], False
    files = []
    signature = [str(workdir)]
    truncated = False
    seen = set()
    for raw, binding in sorted(bindings.items()):
        candidate = Path(raw)
        try:
            if not candidate.is_absolute() or candidate.is_symlink():
                continue
            base = candidate.resolve(strict=True)
            if base.parent != teams or not base.is_dir():
                continue
        except OSError:
            continue
        # Selector workers live on their own board and appear only once that
        # board really exists. Never materialize a future selector for the UI.
        for board in (base, base.with_name(base.name + "-selection")):
            try:
                task_dir = board / "tasks"
                if board.is_symlink() or task_dir.is_symlink() or not task_dir.is_dir():
                    continue
                if task_dir.resolve().parent != board:
                    continue
                candidates = list(islice(task_dir.glob("*.json"), MAX_TEAM_TASKS + 1))
            except OSError:
                continue
            signature.append((str(board), binding["item_id"], binding["ts"], _stamp(task_dir)))
            if len(candidates) > MAX_TEAM_TASKS:
                truncated = True
            for path in sorted(candidates[:MAX_TEAM_TASKS]):
                if path.name.startswith(".") or path.is_symlink() or path in seen:
                    continue
                if len(files) >= MAX_TEAM_TASKS:
                    truncated = True
                    break
                stamp = _stamp(path)
                signature.append((str(path), stamp))
                files.append((path, board.name, binding, stamp))
                seen.add(path)
    return tuple(signature), files, truncated


def source_signature(sid: str, root: Path, life_dir: Path, bindings: dict) -> tuple:
    return _sources(sid, root, life_dir, bindings)[0]


def source_snapshot(sid: str, root: Path, life_dir: Path, bindings: dict) -> tuple:
    """One traversal, reusable as both the cache signature and the projection input."""
    return _sources(sid, root, life_dir, bindings)


def event_id(owner: str, team_id: str, task_id: str) -> str:
    return "team:" + digest([owner, team_id, task_id])


def project_team_events(sid: str, root: Path, life_dir: Path, bindings: dict, sources: tuple | None = None):
    _signature, files, truncated = (
        sources if sources is not None else _sources(sid, root, life_dir, bindings)
    )
    records = {}
    for path, team_id, binding, stamp in files:
        if stamp is None or stamp[2] > MAX_TASK_BYTES:
            truncated = True
            continue
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_TASK_BYTES + 1)
            if len(raw) > MAX_TASK_BYTES:
                truncated = True
                continue
            task = json.loads(raw)
        except (OSError, ValueError, UnicodeDecodeError):
            truncated = True
            continue
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            continue
        task_id = task["task_id"]
        if not task_id or len(task_id) > 160:
            continue
        owner = binding["item_id"]
        key = (owner, team_id, task_id)
        if key in records and records[key][0] > stamp[3]:
            continue
        reason = text(task.get("reason"))
        objective = text(task.get("objective"), 4000)
        team_role = text(task.get("role"), 80)
        started = _number(task.get("claim_ts"))
        finished = _number(task.get("finished_ts"))
        deps = task.get("deps")
        observation = {
            "id": event_id(owner, team_id, task_id),
            "item_id": owner,
            "type": "team.task",
            "ts": binding["ts"],
            "association": "explicit",
            "title": text(task.get("title"), 240),
            "text": "\n\n".join(value for value in (objective, reason) if value),
            "status": text(task.get("state"), 40),
            "role": "reviewer" if team_role in {"idea-review", "reviewer"} else "engineer",
            "team_id": team_id,
            "team_task_id": task_id,
            "team_role": team_role,
            "deps": [event_id(owner, team_id, dep) for dep in deps if isinstance(dep, str)]
            if isinstance(deps, list) else [],
            "reason": reason,
            "owner": text(task.get("owner"), 160),
            "attempt": int(_number(task.get("attempts"))),
            "pending_question": text(task.get("pending_question")),
            "started_ts": started or None,
            "finished_ts": finished or None,
            # Heartbeats are liveness receipts, not new semantic evidence. They
            # invalidate the file cache but must not repurchase card summaries.
            "updated_ts": max(started, finished),
        }
        records[key] = (stamp[3], observation)
    return [records[key][1] for key in sorted(records)], truncated, _signature
