"""Skill-lifecycle mission-view event-family reducers.

Covers the Manager/Reviewer-driven Skill placement lifecycle: create/update,
archive, cross-project tidy (source placement), and the aggregate evolution /
history-compaction counters. No Skill quality judgement happens here — this
module only projects the structured fields the Manager/Reviewer already
decided on.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..event_catalog import EventType
from ._reduce_helpers import _integer, _text, _timeline, _upsert


def reduce_skill_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type in {EventType.SKILL_CREATED, EventType.SKILL_UPDATED}:
        skill_id = _text(event, "skill_id") or _text(event, "name")
        if skill_id:
            _upsert(view.setdefault("learned_skills", []), "id", skill_id, {
                "id": skill_id,
                "name": _text(event, "name", 240),
                "version": _integer(event, "version") or 1,
                "scope": _text(event, "scope"),
                "path": _text(event, "path", 500),
                "status": "active",
                "updated_at": ts,
                "mission_id": str(mission.get("id") or ""),
                "mission_title": str(mission.get("title") or "")[:240],
            })
            _timeline(view, event, role="reviewer", title="Capability unlocked" if event_type == EventType.SKILL_CREATED else "Capability upgraded", detail=_text(event, "name"), tone="skill")

    elif event_type == EventType.SKILL_ARCHIVED:
        skill_id = _text(event, "skill_id") or _text(event, "name")
        for skill in view.setdefault("learned_skills", []):
            if skill.get("id") == skill_id:
                skill.update({"status": "archived", "updated_at": ts})

    elif event_type == EventType.SKILL_TIDIED:
        name = _text(event, "name", 240)
        if name:
            skills = view.setdefault("learned_skills", [])
            existing = next((skill for skill in skills if skill.get("name") == name), None)
            patch = {
                "source_path": _text(event, "path", 1000),
                "source_placement": _text(event, "placement"),
                "source_vertical": _text(event, "vertical"),
                "updated_at": ts,
            }
            if existing is not None:
                existing.update(patch)
            else:
                skills.append({
                    "id": name,
                    "name": name,
                    "version": 1,
                    "scope": "",
                    "path": "",
                    "status": "active",
                    **patch,
                })
            _timeline(
                view,
                event,
                role="manager",
                title="Capability promoted to source",
                detail=name,
                tone="skill",
            )

    elif event_type == EventType.SKILL_EVOLUTION_COMPLETED:
        storage = view.setdefault("storage", {})
        for key in ("project_skill_dir", "global_skill_dir"):
            value = _text(event, key, 1000)
            if value:
                storage[key] = value
        for key in ("project_skill_count", "global_skill_count"):
            storage[key] = _integer(event, key)

    elif event_type == EventType.SKILL_HISTORY_COMPRESSED:
        storage = view.setdefault("storage", {})
        storage["skill_history_compressed"] = int(
            storage.get("skill_history_compressed") or 0
        ) + _integer(event, "count")
        storage["skill_history_bytes_saved"] = int(
            storage.get("skill_history_bytes_saved") or 0
        ) + _integer(event, "bytes_saved")
