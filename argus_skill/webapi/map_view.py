"""Read-only projection of mission records for the map, independent of scheduling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.secret_guard import redact_secrets_text
from ..core.session import read_session_meta
from ..life.memory import LifeMemory

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
    "acceptance_check",
)
EVENT_PREFIXES = (
    "life.mission.",
    "life.phase.",
    "life.planner.task_added",
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


def normalize_events(rows: list[dict], task_ids: set[str]) -> list[dict]:
    active: set[str] = set()
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
            result.append(e)
        if kind in ("life.mission.completed", "life.mission.failed", "life.mission.orphaned"):
            active.discard(owner)
    return list({e["id"]: e for e in result}.values())


def read_map(sid: str, root: Path, life_dir: Path) -> dict:
    memory = LifeMemory.open(life_dir)
    tasks = []
    for item in memory.backlog.history():
        raw = item.to_jsonable()
        task = {k: raw[k] for k in TASK_FIELDS if k in raw}
        for k, v in list(task.items()):
            if isinstance(v, str):
                task[k] = text(v)
        task["summary"] = task.get("last_error") or task.get("notes") or ""
        task["role"] = "engineer"
        task["revision"] = digest(task)
        tasks.append(task)
    tasks.sort(key=lambda t: (t.get("ts") or 0, t["id"]))
    rows = []
    path = life_dir / "events.jsonl"
    truncated = False
    if path.is_file():
        with path.open("rb") as f:
            size = path.stat().st_size
            start = max(0, size - 8 * 1024 * 1024)
            truncated = start > 0
            f.seek(start)
            if start:
                f.readline()
            for line in f.read(8 * 1024 * 1024).splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
    events = normalize_events(rows, {t["id"] for t in tasks})
    meta = read_session_meta(root, sid)
    return {
        "id": f"live:{sid}",
        "title": meta.display_name if meta else sid,
        "kind": "live",
        "description": "",
        "read_only": False,
        "tasks": tasks,
        "events": events[-2000:],
        "coverage": {"truncated": truncated or len(events) > 2000},
    }
