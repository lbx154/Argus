"""Mission View schema defaults and the event types it is computed from.

The view itself is never stored: :func:`load_mission_view` computes it from
the project's event log (see ``_replay``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..event_catalog import EventType, canonical_event_type

MISSION_VIEW_SCHEMA_VERSION = 7
MISSION_TIMELINE_LIMIT = 120
MISSION_ROLE_WORK_LIMIT_PER_ROLE = 40
MISSION_SKILL_CONTENT_MAX_BYTES = 128 * 1024

_ROLE_NAMES = ("manager", "planner", "engineer", "reviewer")
_PIPELINE_ROLE_NAMES = frozenset({"planner", "engineer", "reviewer"})

def empty_mission_view() -> dict[str, Any]:
    return {
        "schema_version": MISSION_VIEW_SCHEMA_VERSION,
        "bootstrapped": False,
        # "zh" or "en" once the operator's request has been seen; sentences in
        # the view are written in this language.
        "language": "",
        "mission": {
            "id": "",
            "title": "",
            "objective": "",
            "summary": "",
            "final_output": "",
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": 0.0,
            "campaign_started_at": None,
            "campaign_elapsed_seconds": 0.0,
        },
        "stage": {"id": "", "label": ""},
        "routing": {
            "route": "",
            "vertical": "",
            "workflow_mode": "",
            "lifetime": "",
            "continuous": False,
            "open_ended": False,
        },
        "round": {"current": 0, "max": 0},
        "active_role": "",
        "roles": [
            {
                "role": role,
                "status": "waiting",
                "kind": "waiting",
                "label": "Waiting",
                "updated_at": 0.0,
            }
            for role in _ROLE_NAMES
        ],
        "role_work": [],
        "dag": [],
        "timeline": [],
        # The team's milestones for the conversation (``_milestones``).
        "milestones": [],
        "artifacts": [],
        "learned_skills": [],
        "learned_wiki_pages": [],
        "storage": {
            "project_skill_dir": "",
            "global_skill_dir": "",
            "project_skill_count": 0,
            "global_skill_count": 0,
            "skill_history_compressed": 0,
            "wiki_retired_compressed": 0,
            "skill_history_bytes_saved": 0,
            "wiki_retired_bytes_saved": 0,
            "wiki_paths": [],
        },
        "achievement": None,
        "review": {"status": "", "reason": "", "rejected_attempts": 0},
        # One structured receipt links completion, chat, and the right-side
        # result surface.  It is null until a successful mission settles.
        "delivery": None,
        "outcome": {},
        "last_event_ts": 0.0,
        "updated_at": 0.0,
    }


def load_mission_view(root: Path | str) -> dict[str, Any]:
    from ._replay import load_reconciled_view

    return load_reconciled_view(Path(root).expanduser())


_PROJECTED_EVENT_TYPES = frozenset({
    EventType.LIFE_MANAGER_INTENT_STARTED,
    EventType.LIFE_MANAGER_INTENT_COMPLETED,
    EventType.LIFE_MANAGER_INTENT_FAILED,
    EventType.LIFE_MANAGER_STAGE_DECISION,
    EventType.MANAGER_TURN_STARTED,
    EventType.LIFE_PLANNER_START,
    EventType.LIFE_PLANNER_TASK_ADDED,
    EventType.LIFE_PLANNER_VERDICT,
    EventType.LIFE_PLANNER_IDLE,
    EventType.LIFE_PLANNER_TERMINAL_IDLE,
    EventType.LIFE_PLANNER_ERROR,
    EventType.LIFE_MISSION_STARTED,
    EventType.LIFE_MISSION_COMPLETED,
    EventType.LIFE_MISSION_FAILED,
    EventType.ROUND_START,
    EventType.ROUND_MAIN_COMPLETED,
    EventType.ROUND_REVIEW_STARTED,
    EventType.ROUND_REVIEW_DEFERRED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.ENGINEER_PROGRESS,
    EventType.IDEA_SEARCH_STARTED,
    EventType.IDEA_SEARCH_COMPLETED,
    EventType.VENUE_RESEARCH_STARTED,
    EventType.VENUE_RESEARCH_COMPLETED,
    EventType.RESEARCH_ACHIEVEMENT_CERTIFIED,
    EventType.SKILL_CREATED,
    EventType.SKILL_UPDATED,
    EventType.SKILL_ARCHIVED,
    EventType.SKILL_TIDIED,
    EventType.SKILL_EVOLUTION_COMPLETED,
    EventType.SKILL_HISTORY_COMPRESSED,
    EventType.WIKI_INITIALIZED,
    EventType.WIKI_EVOLUTION_COMPLETED,
    EventType.WIKI_CREATED,
    EventType.WIKI_UPDATED,
    EventType.WIKI_RETIRED,
    EventType.WIKI_PROMOTION_PROMOTED,
    EventType.WIKI_PROMOTION_DEMOTED,
    EventType.WIKI_RETIRED_COMPRESSED,
})


def mission_view_handles_event(event_type: Any) -> bool:
    return canonical_event_type(event_type) in _PROJECTED_EVENT_TYPES
