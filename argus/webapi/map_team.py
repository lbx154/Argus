"""Read-only Team taskboard observations nested under their recorded mission."""
from __future__ import annotations

import json
import math
import re
from itertools import islice
from pathlib import Path

from ..core.session import read_session_meta, resolve_session_workdir
from .map_view import digest, text

FORMATION_BYTES = 8 * 1024 * 1024
MAX_TEAM_BINDINGS = 32
MAX_TEAM_TASKS = 512
MAX_TASK_BYTES = 256 * 1024
# What a subtask concluded lives beside the board, not in its task file: the
# reviewer's verdict in artifacts/reviews/<route>.json, the route's own title
# in artifacts/routes/<route>.md, and the one chosen route in the selection
# board's artifacts/selection.json. Bounded like the task files.
MAX_TEAM_ARTIFACTS = 64
ARTIFACT_HEAD_BYTES = 4096
SELECTION_BOARD_SUFFIX = "-selection"
_VERDICTS = frozenset({"qualified", "rejected"})
_ROUTE_HEADING = re.compile(r"^(?:route|路线)\s*[-_ ]?\d+\s*[:：—–-]\s*", re.IGNORECASE)


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
        binding = {"item_id": owner, "ts": _number(row.get("ts"))}
        width = int(_number(row.get("width") or row.get("route_count")))
        if width:
            binding["width"] = width
        bindings[path] = binding
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
        return (), [], [], False
    try:
        from ..core.campaign_workdir import active_campaign_workdir

        workdir = resolve_session_workdir(read_session_meta(root, sid), state_dir=life_dir)
        workdir = active_campaign_workdir(life_dir, workdir) or workdir
        teams = (workdir / ".argus" / "teams").resolve()
        if not teams.is_relative_to(workdir):
            return (str(workdir), "outside-workdir"), [], [], False
    except (OSError, ValueError):
        return ("unavailable-workdir",), [], [], False
    files = []
    formations = []
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
            for artifact in _artifact_paths(board):
                signature.append((str(artifact), _stamp(artifact)))
            if board == base:
                # Formation visibility follows exactly the evidence already in
                # the signature, so a cached read stays a cached read.
                formations.append((base.name, binding))
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
    return tuple(signature), files, formations, truncated


def source_signature(sid: str, root: Path, life_dir: Path, bindings: dict) -> tuple:
    return _sources(sid, root, life_dir, bindings)[0]


def source_snapshot(sid: str, root: Path, life_dir: Path, bindings: dict) -> tuple:
    """One traversal, reusable as both the cache signature and the projection input."""
    return _sources(sid, root, life_dir, bindings)


def _artifact_paths(board: Path) -> list[Path]:
    """The conclusion files a board may hold, in a fixed order, never followed
    through symlinks and never more than a handful."""
    found: list[Path] = []
    artifacts = board / "artifacts"
    try:
        if artifacts.is_symlink() or not artifacts.is_dir():
            return found
        selection = artifacts / "selection.json"
        if selection.is_file() and not selection.is_symlink():
            found.append(selection)
        for folder, suffix in (("reviews", "*.json"), ("routes", "*.md")):
            directory = artifacts / folder
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in sorted(islice(directory.glob(suffix), MAX_TEAM_ARTIFACTS)):
                if not path.name.startswith(".") and not path.is_symlink() and path.is_file():
                    found.append(path)
    except OSError:
        return found
    return found


def _read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_TASK_BYTES:
            return {}
        with path.open("rb") as stream:
            value = json.loads(stream.read(MAX_TASK_BYTES))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _route_title(path: Path) -> str:
    """The route's own heading, without the "Route 02:" the writer prefixed."""
    try:
        with path.open("rb") as stream:
            head = stream.read(ARTIFACT_HEAD_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""
    for line in head.splitlines():
        if line.startswith("#"):
            return text(_ROUTE_HEADING.sub("", line.lstrip("#").strip()), 160)
    return ""


def base_team_id(team_id: str) -> str:
    if team_id.endswith(SELECTION_BOARD_SUFFIX) and len(team_id) > len(SELECTION_BOARD_SUFFIX):
        return team_id[: -len(SELECTION_BOARD_SUFFIX)]
    return team_id


def team_conclusions(boards: dict[str, Path]) -> tuple[dict, dict, dict]:
    """Verdicts, route titles and selections per base team, from the boards' artifacts."""
    reviews: dict[tuple[str, str], dict] = {}
    routes: dict[tuple[str, str], str] = {}
    selections: dict[str, dict] = {}
    for team_id, board in boards.items():
        base = base_team_id(team_id)
        for path in _artifact_paths(board):
            if path.name == "selection.json" and team_id != base:
                doc = _read_json(path)
                route = text(doc.get("route_id"), 80)
                if not route:
                    continue
                rejections = doc.get("rejections")
                selections[base] = {
                    "selected_route": route,
                    "rationale": text(doc.get("rationale"), 600),
                    "rejections": {
                        text(key, 80): text(value, 300)
                        for key, value in (rejections.items() if isinstance(rejections, dict) else ())
                        if isinstance(key, str) and isinstance(value, str)
                    },
                }
            elif path.parent.name == "reviews" and team_id == base:
                doc = _read_json(path)
                verdict = text(doc.get("verdict"), 40).lower()
                if verdict not in _VERDICTS:
                    continue
                concerns = doc.get("fatal_concerns")
                reviews[(base, text(doc.get("route_id"), 80) or path.stem)] = {
                    "verdict": verdict,
                    "summary": text(doc.get("summary"), 600),
                    "concerns": len(concerns) if isinstance(concerns, list) else 0,
                }
            elif path.parent.name == "routes" and team_id == base:
                title = _route_title(path)
                if title:
                    routes[(base, path.stem)] = title
    return reviews, routes, selections


def team_outcome(
    team_id: str, role: str, target: str, conclusions: tuple[dict, dict, dict],
) -> dict | None:
    """What one subtask concluded, when its board's artifacts say so."""
    reviews, routes, selections = conclusions
    base = base_team_id(team_id)
    if role == "idea-review":
        review = reviews.get((base, target))
        return {"kind": "review", **review} if review else None
    if role == "idea-route":
        review = reviews.get((base, target))
        selection = selections.get(base)
        outcome: dict = {"kind": "route"}
        if (base, target) in routes:
            outcome["title"] = routes[(base, target)]
        if review:
            outcome["verdict"] = review["verdict"]
        if selection:
            outcome["selected"] = selection["selected_route"] == target
            rejection = selection["rejections"].get(target)
            if rejection:
                outcome["rejection"] = rejection
        return outcome if len(outcome) > 1 else None
    if role == "idea-selector":
        selection = selections.get(base)
        if not selection:
            return None
        chosen = selection["selected_route"]
        return {
            "kind": "selection",
            "selected_route": chosen,
            "selected_title": routes.get((base, chosen), ""),
            "rationale": selection["rationale"],
            "rejected": sorted(selection["rejections"]),
        }
    return None


def event_id(owner: str, team_id: str, task_id: str) -> str:
    return "team:" + digest([owner, team_id, task_id])


def formation_event_id(owner: str, team_id: str, ts: float) -> str:
    # Formation is journal history, not a replaceable Team observation, so the
    # id stays outside the removable "team:" namespace (map_feed only ever
    # broadcasts removals for "team:"-prefixed ids).
    return "formation:" + digest([owner, team_id, ts])


def formation_events(formations: list) -> list[dict]:
    events = {}
    for team_id, binding in formations:
        observation = {
            "id": formation_event_id(binding["item_id"], team_id, binding["ts"]),
            "item_id": binding["item_id"],
            "type": "idea.portfolio.formed",
            "ts": binding["ts"],
            "association": "explicit",
            "role": "engineer",
            # Locale-agnostic projection: the frontend labels formations from
            # the structured fields; there is no server-authored sentence.
            "text": "",
            "team_id": text(team_id, 160),
        }
        if binding.get("width"):
            observation["width"] = int(binding["width"])
        events.setdefault(observation["id"], observation)
    return list(events.values())


def project_team_events(sid: str, root: Path, life_dir: Path, bindings: dict, sources: tuple | None = None):
    _signature, files, formations, truncated = (
        sources if sources is not None else _sources(sid, root, life_dir, bindings)
    )
    records = {}
    boards: dict[str, Path] = {}
    for path, team_id, _binding, _stamp in files:
        boards.setdefault(team_id, path.parent.parent)
    conclusions = team_conclusions(boards)
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
        pause_reason = text(task.get("pause_reason"))
        state = text(task.get("state"), 40)
        if state == "pending" and pause_reason:
            # Turned away by the provider or the budget and waiting to retry:
            # unfinished, not failed, and not merely "not started yet".
            state = "paused"
            reason = reason or pause_reason
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
            "status": state,
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
        outcome = team_outcome(team_id, team_role, text(task.get("target"), 80), conclusions)
        if outcome:
            observation["team_outcome"] = outcome
        records[key] = (stamp[3], observation)
    return [
        *formation_events(formations),
        *(records[key][1] for key in sorted(records)),
    ], truncated, _signature
