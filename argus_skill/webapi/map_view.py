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

# The agent's own words while it works. A narration opens a work segment; the
# tool calls that follow belong to it, until the next narration or the end of
# the round. This is what lets any task's process appear on the map without
# the map knowing what kind of task it is.
NARRATION_KINDS = frozenset({"agent_message", "assistant_message", "message"})
STEP_KINDS = frozenset({"tool_use", "command_execution", "file_change"})
SEGMENT_CLOSERS = (
    "round.main.completed",
    "round.review.",
    "life.mission.completed",
    "life.mission.failed",
    "life.mission.orphaned",
)
SEGMENT_STEP_LIMIT = 40
SEGMENT_LIMIT_PER_ITEM = 80
SEGMENT_TEXT_LIMIT = 1200
STEP_LABEL_LIMIT = 160
TURN_LIMIT = 200


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


def _timestamp(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _step_from_progress(row: dict) -> dict:
    step = {
        "kind": str(row.get("kind") or ""),
        "label": text(row.get("text") or row.get("action_summary"), STEP_LABEL_LIMIT),
        "ts": _timestamp(row.get("ts")),
    }
    for key in ("tool_name", "status", "call_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            step["tool" if key == "tool_name" else key] = text(value, 120)
    return step


def _close_segment(segments: dict, owner: str) -> None:
    segment = segments.get("open", {}).pop(owner, None)
    if segment is not None:
        segments.setdefault("closed", []).append(segment)


def fold_progress(segments: dict, owner: str, row: dict, association: str) -> None:
    """Fold one ``engineer.progress`` row into the owner's open work segment.

    ``segments`` is ``{"open": {owner: segment}, "count": {owner: n},
    "closed": [segment, ...]}`` and persists between incremental reads, so a
    segment that is still being worked on keeps growing (same id, new
    revision) instead of being cut at the read boundary.
    """
    kind = str(row.get("kind") or "")
    if row.get("transient") or row.get("final_delivery"):
        return
    if kind not in NARRATION_KINDS and kind not in STEP_KINDS:
        return
    opened = segments.setdefault("open", {})
    counts = segments.setdefault("count", {})
    segment = opened.get(owner)
    ts = _timestamp(row.get("ts"))
    role = str(row.get("agent_layer") or "engineer")
    if kind in NARRATION_KINDS:
        narration = text(row.get("text"), SEGMENT_TEXT_LIMIT).strip()
        if not narration:
            return
        if segment is not None and segment["steps"]:
            _close_segment(segments, owner)
            segment = None
        if segment is not None:
            # Two narrations with no work in between are one thought.
            segment["text"] = text(f"{segment['text']}\n\n{narration}", SEGMENT_TEXT_LIMIT)
            segment["ts_end"] = ts
            return
    if segment is None:
        index = counts.get(owner, 0)
        if index >= SEGMENT_LIMIT_PER_ITEM:
            return
        counts[owner] = index + 1
        segment = opened[owner] = {
            "id": f"seg:{owner}:{index + 1}",
            "item_id": owner,
            "type": "work.segment",
            "ts": ts,
            "ts_end": ts,
            "association": association,
            "role": role,
            "text": "",
            "steps": [],
            "overflow": 0,
        }
    segment["ts_end"] = ts
    if kind in NARRATION_KINDS:
        segment["text"] = text(row.get("text"), SEGMENT_TEXT_LIMIT).strip()
        return
    if len(segment["steps"]) >= SEGMENT_STEP_LIMIT:
        segment["overflow"] += 1
        return
    segment["steps"].append(_step_from_progress(row))


def segment_events(segments: dict) -> list[dict]:
    """Closed segments so far plus every segment still open, oldest first."""
    closed = segments.pop("closed", []) if segments else []
    return [*closed, *sorted(segments.get("open", {}).values(), key=lambda s: s["ts"])]


def normalize_events(
    rows: list[dict], task_ids: set[str], active: set[str] | None = None,
    segments: dict | None = None,
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
            and kind.startswith(("round.", "life.phase.", "agent.message", "engineer.progress"))
            and len(active) == 1
        ):
            owner = next(iter(active))
            association = "single_active_window"
        if segments is not None and owner in task_ids:
            if kind == "engineer.progress":
                fold_progress(segments, owner, row, association)
            elif kind.startswith(SEGMENT_CLOSERS):
                _close_segment(segments, owner)
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
            for key in ("success", "review_skipped", "overall_complete", "campaign_continues"):
                if isinstance(row.get(key), bool):
                    e[key] = row[key]
            if isinstance(row.get("attempt"), int):
                e["attempt"] = row["attempt"]
            outcome = row.get("outcome")
            certification = row.get("stage_certification") or (
                outcome.get("stage_certification") if isinstance(outcome, dict) else None
            )
            if certification:
                e["stage_certification"] = text(certification, 80)
            if kind == "life.plan.node.superseded":
                # Keep the shape lean: structured keys only when present.
                if row.get("reason"):
                    e["reason"] = text(row["reason"])
                if row.get("superseded_by_plan_id"):
                    e["superseded_by_plan_id"] = text(row["superseded_by_plan_id"], 160)
            result.append(e)
        if kind in ("life.mission.completed", "life.mission.failed", "life.mission.orphaned"):
            active.discard(owner)
    if segments is not None:
        result.extend(segment_events(segments))
    return list({e["id"]: e for e in result}.values())


def turn_records(
    rows: list[dict], turns: dict | None = None, asks: dict | None = None,
) -> dict:
    """Single-agent turns as map cards: what was asked, the work, the answer.

    A chat turn that used tools journals its steps on the ``ui.argus`` event;
    the matching ``ui.operator`` row (same ``web-<n>`` id) supplies the ask.
    ``turns`` and ``asks`` persist between incremental reads, so cards derived
    earlier survive a read that only sees new rows, and an ask read on one
    page still meets its reply on the next.
    """
    turns = turns if turns is not None else {}
    asks = asks if asks is not None else {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        message_id = str(row.get("message_id") or "")
        if row.get("type") == "ui.operator" and message_id.endswith("-operator"):
            asks[message_id[: -len("-operator")]] = {
                "ts": row.get("ts"), "text": text(row.get("text"), 4000),
            }
            while len(asks) > 50:
                asks.pop(next(iter(asks)))
            continue
        if row.get("type") != "ui.argus" or not message_id.endswith("-argus"):
            continue
        steps = [step for step in row.get("steps") or [] if isinstance(step, dict) and step.get("label")]
        if not steps:
            continue
        turn_id = message_id[: -len("-argus")]
        ask = asks.pop(turn_id, {})
        asked = text(ask.get("text"), 4000).strip()
        reply = text(row.get("text"), 4000).strip()
        started = min((_timestamp(step.get("started_ts")) for step in steps), default=0.0)
        finished = max((_timestamp(step.get("ended_ts")) for step in steps), default=0.0)
        replied_at = _timestamp(row.get("ts"))
        card_id = f"turn:{turn_id}"
        title = asked.splitlines()[0] if asked else (reply.splitlines()[0] if reply else turn_id)
        turns[card_id] = {
            "card": {
                "id": card_id,
                "kind": "turn",
                "ts": _timestamp(ask.get("ts")) or started or replied_at,
                "title": text(title, 120),
                "objective": asked,
                "status": "done",
                "deps": [],
                "role": "manager",
                "summary": text(reply, 400),
                "started_ts": started or None,
                "finished_ts": replied_at or finished or None,
            },
            "events": [
                {
                    "id": f"{card_id}:work",
                    "item_id": card_id,
                    "type": "work.segment",
                    "ts": started or replied_at,
                    "ts_end": finished or replied_at,
                    "association": "explicit",
                    "role": "manager",
                    "text": "",
                    "steps": [
                        {
                            "kind": str(step.get("kind") or "tool_use"),
                            "label": text(step.get("label"), STEP_LABEL_LIMIT),
                            "ts": _timestamp(step.get("started_ts")),
                            **({"tool": text(step["tool"], 120)} if step.get("tool") else {}),
                            **({"status": text(step["status"], 40)} if step.get("status") else {}),
                        }
                        for step in steps[:SEGMENT_STEP_LIMIT]
                    ],
                    "overflow": max(0, len(steps) - SEGMENT_STEP_LIMIT),
                },
                {
                    "id": f"{card_id}:reply",
                    "item_id": card_id,
                    "type": "turn.replied",
                    "ts": replied_at,
                    "association": "explicit",
                    "role": "manager",
                    "text": reply,
                    "status": "done",
                },
            ],
        }
    while len(turns) > TURN_LIMIT:
        turns.pop(next(iter(turns)))
    return turns


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
            segments: dict = {}
            turns: dict = {}
            asks: dict = {}
            if append:
                previous = state.get("events", [])
                active = set(state.get("active", ()))
                segments = state.get("segments") or {}
                turns = dict(state.get("turns") or {})
                asks = dict(state.get("turn_asks") or {})
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
                segments = {}
                turns = {}
                asks = {}
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
            turns = turn_records(rows, turns, asks)
            events = list({e["id"]: e for e in [
                *previous,
                *normalize_events(rows, task_ids, active, segments),
                *(event for turn in turns.values() for event in turn["events"]),
            ]}.values())
            binding_truncated |= remember_formations(rows, task_ids, bindings)
            bindings = {path: binding for path, binding in bindings.items()
                        if binding["item_id"] in task_ids}
            f.seek(max(0, consumed - 128))
            state.update(
                identity=identity, offset=consumed, anchor=f.read(min(128, consumed)),
                task_ids=task_ids, events=events[-2000:], active=active,
                segments=segments, turns=turns, turn_asks=asks,
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
    for turn in (state.get("turns") or {}).values():
        card = dict(turn["card"])
        card["revision"] = digest(card)
        tasks.append(card)
    tasks.sort(key=lambda t: (t.get("ts") or 0, t["id"]))
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
