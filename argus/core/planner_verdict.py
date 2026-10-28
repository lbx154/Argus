"""Canonical contract for ``life.planner.verdict`` events."""
from __future__ import annotations

from enum import Enum
from typing import Any

from .event_catalog import EventType, validate_event_envelope


class PlannerVerdictStatus(str, Enum):
    PLANNED = "planned"
    COMPLETED = "completed"
    # Waiting; the event's ``park_reason`` says on what (core/stop_kinds.py).
    PARKED = "parked"
    ERROR = "error"


_STATUS_POLICY: dict[PlannerVerdictStatus, tuple[bool, bool]] = {
    PlannerVerdictStatus.PLANNED: (True, False),
    PlannerVerdictStatus.COMPLETED: (True, False),
    PlannerVerdictStatus.PARKED: (False, True),
    PlannerVerdictStatus.ERROR: (False, False),
}

_PROTECTED_FIELDS = frozenset({
    "type",
    "status",
    "success",
    "recoverable",
    "reason",
    "summary",
    "project_id",
    "mission_id",
    "research_target_level",
    "correctness_status",
    "novelty_status",
    "significance_status",
    "stop_kind",
    "completion_kind",
    "park_reason",
    "tasks_added",
})


def build_planner_verdict_event(
    *,
    status: PlannerVerdictStatus,
    reason: str,
    project_id: str = "",
    mission_id: str = "",
    research_target_level: str | None = None,
    correctness_status: str | None = None,
    novelty_status: str | None = None,
    significance_status: str | None = None,
    stop_kind: str | None = None,
    completion_kind: str | None = None,
    park_reason: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build and validate one complete planner-verdict event."""
    success, recoverable = _STATUS_POLICY[status]
    unsafe = _PROTECTED_FIELDS.intersection(details)
    if unsafe:
        raise ValueError(
            "planner verdict details cannot override contract fields: "
            + ", ".join(sorted(unsafe))
        )
    event: dict[str, Any] = {
        "type": EventType.LIFE_PLANNER_VERDICT,
        "status": status.value,
        "success": success,
        "recoverable": recoverable,
        "reason": str(reason),
        "summary": str(reason),
        "project_id": str(project_id),
        "mission_id": str(mission_id),
        "research_target_level": research_target_level,
        "correctness_status": correctness_status,
        "novelty_status": novelty_status,
        "significance_status": significance_status,
        "stop_kind": stop_kind,
        "completion_kind": completion_kind,
        "park_reason": park_reason if status is PlannerVerdictStatus.PARKED else None,
        **details,
    }
    event["tasks_added"] = int(event.get("enqueued_tasks", 0) or 0)
    validation = validate_event_envelope(event, require_known=True)
    if not validation.valid:
        raise ValueError(
            "invalid life.planner.verdict: " + "; ".join(validation.errors)
        )
    return event


__all__ = [
    "PlannerVerdictStatus",
    "build_planner_verdict_event",
]
