"""Shared low-level helpers used by every mission-view event-family reducer.

Kept separate from the family reducers so each family module only imports the
small set of primitives it actually needs (text/number coercion, timeline and
role-work upserts, decision-context capture) without pulling in unrelated
reducer code.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping

from ..event_catalog import canonical_event_type
from ._view_state import (
    _PIPELINE_ROLE_NAMES,
    _ROLE_NAMES,
    MISSION_ROLE_WORK_LIMIT_PER_ROLE,
    MISSION_TIMELINE_LIMIT,
)


def _text(event: Mapping[str, Any], key: str, limit: int = 500) -> str:
    return str(event.get(key) or "").strip()[:limit]


def _number(event: Mapping[str, Any], key: str) -> float | None:
    value = event.get(key)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _integer(event: Mapping[str, Any], key: str) -> int | None:
    value = _number(event, key)
    return int(value) if value is not None else None


def _event_id(event: Mapping[str, Any]) -> str:
    explicit = event.get("event_id") or event.get("id")
    if explicit:
        return str(explicit)
    stable = json.dumps(dict(event), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]


def _upsert(rows: list[dict[str, Any]], key: str, value: str, patch: dict[str, Any]) -> None:
    if not value:
        return
    for index, row in enumerate(rows):
        if str(row.get(key) or "") == value:
            rows[index] = {**row, **patch}
            return
    rows.append(patch)


def _set_role(view: dict[str, Any], role: str, status: str, label: str, ts: float) -> None:
    if role not in _ROLE_NAMES:
        return
    roles = view.setdefault("roles", [])
    if status == "active" and role in _PIPELINE_ROLE_NAMES:
        for existing in roles:
            if (
                existing.get("role") in _PIPELINE_ROLE_NAMES
                and existing.get("role") != role
                and existing.get("status") == "active"
            ):
                existing.update({
                    "status": "done",
                    "label": "Handed off",
                    "updated_at": ts,
                })
    patch = {"role": role, "status": status, "label": label, "updated_at": ts}
    _upsert(roles, "role", role, patch)
    if status == "active":
        view["active_role"] = role
    elif view.get("active_role") == role:
        view["active_role"] = ""


def _timeline(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    role: str,
    title: str,
    detail: str = "",
    tone: str = "neutral",
) -> None:
    rows = view.setdefault("timeline", [])
    event_id = _event_id(event)
    if any(str(row.get("id") or "") == event_id for row in rows):
        return
    row = {
        "id": event_id,
        "ts": float(event.get("ts") or time.time()),
        "type": canonical_event_type(event.get("type")),
        "role": role,
        "title": title[:180],
        "detail": detail[:500],
        "tone": tone,
    }
    for key in ("item_id", "branch_id", "hypothesis_id", "experiment_id", "metric_id"):
        value = _text(event, key, 160)
        if value:
            row[key] = value
    rows.append(row)
    view["timeline"] = rows[-MISSION_TIMELINE_LIMIT:]


def _role_work(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    role: str,
    kind: str,
    title: str,
    detail: str = "",
    status: str = "",
) -> None:
    if role not in _ROLE_NAMES:
        return
    rows = view.setdefault("role_work", [])
    message_id = _text(event, "message_id", 200)
    work_id = f"{role}:{message_id}" if message_id else _event_id(event)
    mission = view.setdefault("mission", {})
    item_id = _text(event, "item_id", 160)
    existing = next(
        (row for row in rows if str(row.get("id") or "") == work_id),
        None,
    )
    if existing is not None and len(str(existing.get("detail") or "")) > len(detail):
        detail = str(existing.get("detail") or "")
    patch = {
        "id": work_id,
        "ts": float(event.get("ts") or time.time()),
        "role": role,
        "kind": kind,
        "title": title[:240],
        "detail": detail[:4000],
        "status": status,
        "item_id": item_id,
        "mission_id": str(mission.get("id") or ""),
        "mission_title": str(mission.get("title") or "")[:240],
        "round_index": _integer(event, "round_index"),
    }
    _upsert(rows, "id", work_id, patch)
    keep_ids: set[str] = set()
    for role_name in _ROLE_NAMES:
        role_rows = [
            row for row in rows if str(row.get("role") or "") == role_name
        ]
        keep_ids.update(
            str(row.get("id") or "")
            for row in role_rows[-MISSION_ROLE_WORK_LIMIT_PER_ROLE:]
        )
    view["role_work"] = [
        row for row in rows if str(row.get("id") or "") in keep_ids
    ]


def _capture_decision_context(
    view: dict[str, Any],
    event: Mapping[str, Any],
) -> None:
    context = view.setdefault("decision_context", {})
    for key in (
        "planner_report",
        "research_result",
        "checklist_feedback",
        "step_back",
    ):
        value = event.get(key)
        if isinstance(value, Mapping):
            context[key] = dict(value)


def _visible_role_work_progress(
    event: Mapping[str, Any],
    *,
    role: str,
    kind: str,
    detail: str,
) -> bool:
    if kind == "reasoning":
        return False
    if (
        kind in {"assistant_message", "agent_message", "message"}
        and role in {"planner", "reviewer"}
        and detail.lstrip().startswith("{")
    ):
        return False
    return True


_PROGRESS_LABELS = {
    "agent_message": "Reporting progress",
    "assistant_message": "Reporting progress",
    "command_execution": "Running a command",
    "reasoning": "Reasoning",
    "tool_use": "Using a tool",
    "tool_result": "Inspecting tool output",
    "codex_idle": "Waiting for model output",
}
