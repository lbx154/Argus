"""Read-only projection of mission records for the map, independent of scheduling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.secret_guard import redact_secrets_text
from ..core.session import read_session_meta
from ..life.memory import LifeMemory, _jsonl_history_paths

TASK_FIELDS = (
    "id",
    "ts",
    "title",
    "objective",
    "status",
    "deps",
    "notes",
    "last_error",
    "pending_question",
    "plan_id",
    "plan_version",
    "attempt",
    "started_ts",
    "finished_ts",
    "superseded_by_plan_id",
    "superseded_reason",
    "node_key",
    "parallel_safe",
    "owns_paths",
    "acceptance_check",
)
EVENT_PREFIXES = (
    "life.mission.",
    "life.phase.",
    "life.planner.task_added",
    # Low-frequency plan-identity transitions; at most one per retired node.
    "life.plan.node.superseded",
    "round.start",
    "round.main.completed",
    "round.review.",
    "agent.message",
)


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:24]


def text(value, limit=6000):
    return redact_secrets_text(str(value or ""))[:limit]


def task_content_revision(task: dict) -> str:
    return digest({k: task.get(k) for k in ("title", "objective", "acceptance_check")})


def with_revisions(value: dict) -> dict:
    return {
        **value,
        "tasks": [
            {**t, "revision": t.get("revision") or digest(t),
             "content_revision": task_content_revision(t)}
            for t in value.get("tasks", [])
        ],
        "events": [
            {**e, "revision": digest({k: v for k, v in e.items() if k != "revision"})}
            for e in value.get("events", [])
        ],
    }


def normalize_events(
    rows: list[dict], task_ids: set[str], active: set[str] | None = None,
) -> list[dict]:
    if active is None:
        active = set()
    result = []
    for row in rows:
        # Summary calls are accounted for, but are not research steps to summarize again.
        if row.get("run_label") == "map-summary":
            continue
        kind = str(row.get("type", ""))
        owner = str(row.get("item_id") or row.get("mission_id") or "")
        if kind == "life.mission.started" and owner:
            active.add(owner)
        association = "explicit"
        if (
            not owner
            and kind.startswith(("round.", "life.phase.", "agent.message"))
            and len(active) == 1
        ):
            owner = next(iter(active))
            association = "single_active_window"
        if owner in task_ids and kind.startswith(EVENT_PREFIXES):
            e = {
                "id": str(row.get("event_id") or digest(row)),
                "item_id": owner,
                "type": kind,
                "ts": row.get("ts", 0),
                "association": association,
                "role": str(
                    row.get("agent_layer")
                    or row.get("role")
                    or ("reviewer" if "review" in kind else "engineer")
                ),
                "text": text(
                    row.get("summary")
                    or row.get("reason")
                    or row.get("text")
                    or row.get("last_message")
                    or row.get("message")
                ),
                "status": text(row.get("status"), 40),
                "next_action": text(row.get("next_action")),
            }
            number = row.get("round_index", row.get("round"))
            if isinstance(number, int) and 0 <= number < 10000:
                e["round_index"] = number
            if isinstance(row.get("success"), bool):
                e["success"] = row["success"]
            if isinstance(row.get("review_skipped"), bool):
                e["review_skipped"] = row["review_skipped"]
            if kind == "life.plan.node.superseded":
                # Keep the shape lean: structured keys only when present.
                if row.get("reason"):
                    e["reason"] = text(row["reason"])
                if row.get("superseded_by_plan_id"):
                    e["superseded_by_plan_id"] = text(row["superseded_by_plan_id"], 160)
            result.append(e)
        if kind in ("life.mission.completed", "life.mission.failed", "life.mission.orphaned"):
            active.discard(owner)
    return list({e["id"]: e for e in result}.values())


def read_map(
    sid: str, root: Path, life_dir: Path, *, event_state: dict | None = None,
    include_events: bool = True, team_sources: tuple | None = None,
) -> dict:
    from .map_team import previous_formations, project_team_events, remember_formations

    memory = LifeMemory.open(life_dir)
    tasks = []
    for item in memory.backlog.history():
        raw = item.to_jsonable()
        task = {k: raw[k] for k in TASK_FIELDS if k in raw}
        for k, v in list(task.items()):
            if isinstance(v, str):
                task[k] = text(v)
            elif isinstance(v, list):
                # owns_paths (and deps) are operator-visible strings too.
                task[k] = [text(x) if isinstance(x, str) else x for x in v]
        task["summary"] = task.get("last_error") or task.get("notes") or ""
        task["role"] = "engineer"
        task["revision"] = digest(task)
        tasks.append(task)
    tasks.sort(key=lambda t: (t.get("ts") or 0, t["id"]))
    rows = []
    path = life_dir / "events.jsonl"
    truncated = False
    task_ids = {t["id"] for t in tasks}
    state = event_state if event_state is not None else {}
    previous = []
    active: set[str] = set()
    if include_events and path.is_file():
        with path.open("rb") as f:
            stat = path.stat()
            size = stat.st_size
            identity = (stat.st_dev, stat.st_ino)
            offset = state.get("offset", 0)
            append = (
                state.get("identity") == identity and size >= offset
                and state.get("task_ids") == task_ids
            )
            if append and offset:
                f.seek(max(0, offset - 128))
                append = f.read(min(128, offset)) == state.get("anchor")
            retained_previous = False
            if not append and state.get("identity") and state["identity"] != identity:
                for archived in _jsonl_history_paths(path):
                    if archived == path:
                        continue
                    archived_stat = archived.stat()
                    if (archived_stat.st_dev, archived_stat.st_ino) == state["identity"]:
                        retained_previous = True
                        break
            if append:
                previous = state.get("events", [])
                active = set(state.get("active", ()))
            bindings = dict(state.get("team_bindings", {})) if append or retained_previous else {}
            binding_truncated = bool(state.get("team_bindings_truncated")) if append or retained_previous else False
            if not append:
                binding_truncated |= remember_formations(previous_formations(life_dir), task_ids, bindings)
            start = max(0, size - 8 * 1024 * 1024)
            if append:
                start = max(start, offset)
            truncated = start > 0 and (
                not append or start > offset or state.get("truncated", False)
            )
            f.seek(start)
            if start and (not append or start != offset):
                f.readline()
                active.clear()
            consumed = f.tell()
            for line in f.read(8 * 1024 * 1024).splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    continue
                consumed += len(line)
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
            events = list({e["id"]: e for e in [
                *previous, *normalize_events(rows, task_ids, active),
            ]}.values())
            binding_truncated |= remember_formations(rows, task_ids, bindings)
            bindings = {path: binding for path, binding in bindings.items()
                        if binding["item_id"] in task_ids}
            f.seek(max(0, consumed - 128))
            state.update(
                identity=identity, offset=consumed, anchor=f.read(min(128, consumed)),
                task_ids=task_ids, events=events[-2000:], active=active,
                team_bindings=bindings,
                team_bindings_truncated=binding_truncated,
                truncated=truncated or len(events) > 2000,
                reset=bool(state) and not append and not retained_previous
                and state.get("task_ids") == task_ids,
            )
    else:
        events = []
        was_present = bool(state)
        state.clear()
        state["reset"] = was_present
    meta = read_session_meta(root, sid)
    bindings = state.get("team_bindings", {})
    # team_sources is a (bindings, traversal) pair captured by the caller
    # before this read; it is only reusable while that evidence is unchanged.
    team_events, team_truncated, team_signature = project_team_events(
        sid, root, life_dir, bindings,
        sources=team_sources[1]
        if team_sources is not None and team_sources[0] == bindings else None,
    ) if include_events else ([], False, ())
    state["team_signature"] = team_signature
    return with_revisions({
        "id": f"live:{sid}",
        "title": meta.display_name if meta else sid,
        "kind": "live",
        "description": "",
        "read_only": False,
        "tasks": tasks,
        "events": [*events[-2000:], *team_events],
        "coverage": {"truncated": truncated or len(events) > 2000 or team_truncated
                     or bool(state.get("team_bindings_truncated"))},
    })
