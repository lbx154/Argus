"""Stable dispatcher for ``reduce_mission_view_event``.

The dispatcher is a plain ``event_type -> handler`` table built once at
import time. ``reduce_mission_view_event`` keeps doing the shared preamble
(canonicalizing the event type, stamping ``last_event_ts``, capturing
decision context) and the shared tail (refreshing the primary metric,
stamping ``updated_at``) itself — those are cross-cutting, not
family-specific — then looks up and calls at most one family reducer for the
event's type. This preserves the original "at most one branch runs per
event" semantics of the historical if/elif chain exactly, since the table is
keyed by (canonical) event type and every entry in the old chain mapped to
exactly one branch.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..event_catalog import EventType, canonical_event_type
from ._reduce_manager import reduce_manager_event, reduce_planner_event
from ._reduce_mission import reduce_mission_lifecycle_event, reduce_round_event
from ._reduce_research import reduce_achievement_event
from ._reduce_skill import reduce_skill_event
from ._reduce_wiki import reduce_wiki_event
from ._view_state import (
    _locked,
    _read_unlocked,
    _write_unlocked,
    load_mission_view,
    mission_view_handles_event,
)

_FamilyReducer = Callable[..., None]


def _handlers_for(reducer: _FamilyReducer, *event_types: str) -> dict[str, _FamilyReducer]:
    return {event_type: reducer for event_type in event_types}


_EVENT_HANDLERS: dict[str, _FamilyReducer] = {
    **_handlers_for(
        reduce_manager_event,
        EventType.LIFE_MANAGER_INTENT_STARTED,
        EventType.LIFE_MANAGER_INTENT_COMPLETED,
        EventType.LIFE_MANAGER_INTENT_FAILED,
        EventType.LIFE_MANAGER_STAGE_DECISION,
    ),
    **_handlers_for(
        reduce_planner_event,
        EventType.LIFE_PLANNER_START,
        EventType.LIFE_PLANNER_TASK_ADDED,
        EventType.LIFE_PLANNER_VERDICT,
        EventType.LIFE_PLANNER_WAITING,
        EventType.LIFE_PLANNER_TERMINAL_IDLE,
        EventType.LIFE_PLANNER_ERROR,
    ),
    **_handlers_for(
        reduce_mission_lifecycle_event,
        EventType.LIFE_MISSION_STARTED,
        EventType.LIFE_MISSION_COMPLETED,
        EventType.LIFE_MISSION_FAILED,
    ),
    **_handlers_for(
        reduce_round_event,
        EventType.ROUND_START,
        EventType.ENGINEER_PROGRESS,
        EventType.ROUND_REVIEW_STARTED,
        EventType.ROUND_MAIN_COMPLETED,
        EventType.ROUND_REVIEW_DEFERRED,
        EventType.ROUND_REVIEW_COMPLETED,
        EventType.IDEA_SEARCH_STARTED,
        EventType.IDEA_SEARCH_COMPLETED,
        EventType.VENUE_RESEARCH_STARTED,
        EventType.VENUE_RESEARCH_COMPLETED,
    ),
    **_handlers_for(
        reduce_achievement_event,
        EventType.RESEARCH_ACHIEVEMENT_CERTIFIED,
    ),
    **_handlers_for(
        reduce_skill_event,
        EventType.SKILL_CREATED,
        EventType.SKILL_UPDATED,
        EventType.SKILL_ARCHIVED,
        EventType.SKILL_TIDIED,
        EventType.SKILL_EVOLUTION_COMPLETED,
        EventType.SKILL_HISTORY_COMPRESSED,
    ),
    **_handlers_for(
        reduce_wiki_event,
        EventType.WIKI_INITIALIZED,
        EventType.WIKI_EVOLUTION_COMPLETED,
        EventType.WIKI_RETIRED_COMPRESSED,
        EventType.WIKI_CREATED,
        EventType.WIKI_UPDATED,
        EventType.WIKI_RETIRED,
        EventType.WIKI_PROMOTION_PROMOTED,
        EventType.WIKI_PROMOTION_DEMOTED,
    ),
}


def reduce_mission_view_event(view: dict[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    event_type = canonical_event_type(event.get("type"))
    ts = float(event.get("ts") or time.time())
    view["last_event_ts"] = max(float(view.get("last_event_ts") or 0.0), ts)
    mission = view.setdefault("mission", {})

    handler = _EVENT_HANDLERS.get(event_type)
    if handler is not None:
        handler(view, event, event_type=event_type, ts=ts, mission=mission)

    view["updated_at"] = time.time()
    return view


def update_mission_view_event(root: Path | str, event: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(root).expanduser()
    if not mission_view_handles_event(event.get("type")):
        return load_mission_view(path)
    with _locked(path):
        view = reduce_mission_view_event(_read_unlocked(path), event)
        _write_unlocked(path, view)
        return view
