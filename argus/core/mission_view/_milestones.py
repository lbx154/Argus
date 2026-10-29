"""The team's milestones, computed from the event log with the rest of the view.

A milestone is one recorded fact a person following the work wants to see
without reading tool calls: a task started or put back in the queue, a round
started or handed over, a review's verdict with the Reviewer's own reason,
files the Engineer wrote, an error or hold that stopped the work. Each row
keeps the event's own fields (the verdict and reason are the Reviewer's words,
never reworded); the conversation and the Manager's status context read them.

Task results and stage moves are not repeated here: the team already tells
the operator those in the chat (``core.team_reports``).
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ..event_catalog import EventType
from ._reduce_helpers import _event_id, _integer, _text
from ._reduce_mission import _settled_status

MILESTONE_LIMIT = 80
MILESTONE_TEXT_LIMIT = 400
MILESTONE_PATH_LIMIT = 12

# Holds and errors that stop the team's work; the frontend says each in its own words.
_STOPPED = frozenset({
    EventType.LIFE_BUDGET_PAUSE,
    EventType.LIFE_LIFECYCLE_BLOCK,
    EventType.LIFE_SUPERVISOR_ERROR,
    EventType.LIFE_AUTH_FAILURE,
    EventType.LIFE_EXECUTION_HOST_BLOCKED,
})
MILESTONE_EVENT_TYPES = frozenset({
    EventType.LIFE_MISSION_STARTED,
    EventType.LIFE_MISSION_REQUEUED,
    EventType.ROUND_START,
    EventType.ROUND_MAIN_COMPLETED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.ENGINEER_PROGRESS,
    *_STOPPED,
})
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.MULTILINE)
_WRITE_TOOLS = frozenset({"create", "write", "write_file", "edit", "str_replace", "str_replace_editor"})


def _written_paths(event: Mapping[str, Any]) -> list[str]:
    """The files an Engineer's tool call wrote, as the call names them."""
    kind = _text(event, "kind", 40)
    if kind == "file_change":
        changes = event.get("changes")
        rows = changes if isinstance(changes, list) else []
        return [str(row.get("path") if isinstance(row, dict) else row or "").strip() for row in rows]
    if kind != "tool_use":
        return []
    tool = _text(event, "tool_name", 80)
    text = str(event.get("text") or "")
    if tool == "apply_patch":
        return _PATCH_FILE.findall(text)
    if tool not in _WRITE_TOOLS:
        return []
    try:
        args = json.loads(text.partition(": ")[2])
    except ValueError:
        return []
    return [str(args.get("path") or args.get("file_path") or "")] if isinstance(args, dict) else []


def _round_of(rows: list[dict[str, Any]], item_id: str) -> int | None:
    for row in reversed(rows):
        if row.get("kind") == "round_started" and row.get("item_id") == item_id:
            return row.get("round")
    return None


def _files(rows: list[dict[str, Any]], event: Mapping[str, Any], ts: float) -> None:
    role = _text(event, "agent_layer", 40) or _text(event, "actor", 80)
    if not role.startswith("engineer"):
        return
    paths = [path for path in _written_paths(event) if path]
    item_id = _text(event, "item_id", 160)
    if not paths or not item_id:
        return
    number = _round_of(rows, item_id)
    row_id = f"files:{item_id}:{number or 0}"
    row = next((row for row in rows if row.get("id") == row_id), None)
    if row is None:
        row = {"id": row_id, "kind": "files", "item_id": item_id, "round": number, "paths": []}
    else:
        rows.remove(row)
    # Newest last, each file once; the row moves to when the last one was written.
    kept = [path for path in row["paths"] if path not in paths] + list(dict.fromkeys(paths))
    rows.append({**row, "ts": ts, "paths": kept[-MILESTONE_PATH_LIMIT:]})


def reduce_milestone(view: dict[str, Any], event: Mapping[str, Any], event_type: str) -> None:
    """Add ``event``'s milestone to ``view["milestones"]``, if it is one."""
    if event_type not in MILESTONE_EVENT_TYPES:
        return
    rows: list[dict[str, Any]] = view.setdefault("milestones", [])
    ts = float(event.get("ts") or 0.0)
    if event_type == EventType.ENGINEER_PROGRESS:
        _files(rows, event, ts)
        del rows[:-MILESTONE_LIMIT]
        return
    row: dict[str, Any] = {
        "id": _event_id(event),
        "ts": ts,
        "type": event_type,
        "item_id": _text(event, "item_id", 160),
    }
    if any(existing.get("id") == row["id"] for existing in rows):
        return
    if event_type == EventType.LIFE_MISSION_STARTED:
        row.update(kind="task_started", title=_text(event, "title", 240))
    elif event_type == EventType.LIFE_MISSION_REQUEUED:
        row.update(kind="task_requeued", title=_text(event, "title", 240),
                   error=_text(event, "error", MILESTONE_TEXT_LIMIT))
    elif event_type == EventType.ROUND_START:
        row.update(kind="round_started", round=_integer(event, "round_index"))
    elif event_type == EventType.ROUND_MAIN_COMPLETED:
        failed = event.get("exit_code") not in (None, 0) or bool(event.get("fatal_error"))
        row.update(kind="round_finished", round=_integer(event, "round_index"), failed=failed)
    elif event_type == EventType.ROUND_REVIEW_COMPLETED:
        skipped = event.get("review_skipped") is True
        row.update(
            kind="review", round=_integer(event, "round_index"),
            status="skipped" if skipped else _settled_status(event),
            source=_text(event, "review_source", 60) or "reviewer",
            reason=_text(event, "reason", MILESTONE_TEXT_LIMIT),
        )
    else:
        row.update(
            kind="stopped",
            text=_text(event, "text", MILESTONE_TEXT_LIMIT),
            reason=_text(event, "reason", MILESTONE_TEXT_LIMIT),
            stop_kind=_text(event, "stop_kind", 60),
        )
    rows.append(row)
    del rows[:-MILESTONE_LIMIT]


__all__ = ["MILESTONE_EVENT_TYPES", "MILESTONE_LIMIT", "reduce_milestone"]
