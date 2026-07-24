"""Reviewer-certified research achievement projection."""
from __future__ import annotations

from typing import Any, Mapping

from ._reduce_helpers import _text


def reduce_achievement_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    _ = event_type, mission
    current_mission = view.get("mission", {})
    started = current_mission.get("started_at")
    completed = current_mission.get("completed_at")
    elapsed = (
        max(0.0, float(completed) - float(started))
        if started and completed
        else 0.0
    )
    evidence = list(event.get("evidence") or [])
    view["achievement"] = {
        "id": _text(event, "achievement_id"),
        "title": _text(event, "title", 240),
        "goal": _text(event, "goal", 2000),
        "summary": _text(event, "summary", 2000),
        "rejected_attempts": int(
            view.get("review", {}).get("rejected_attempts") or 0
        ),
        "skills_learned": sum(
            1
            for row in view.get("learned_skills", [])
            if row.get("status") == "active"
        ),
        "artifacts": len(evidence),
        "elapsed_seconds": elapsed,
        "evidence": evidence,
        "reviewer_certified": True,
        "certified_at": ts,
    }
