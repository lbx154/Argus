"""Canonical contract for ``life.planner.verdict`` events."""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from .event_catalog import EventType, validate_event_envelope
from .research_contract import (
    normalize_research_result,
    normalize_research_target_level,
)
from .stop_kinds import normalize_stop_kind, park_reason_of


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


_PARKED = PlannerVerdictStatus.PARKED


def _legacy_status(
    payload: Mapping[str, Any],
    *,
    research_result: dict[str, Any] | None,
) -> tuple[PlannerVerdictStatus, str | None]:
    """The status an older verdict settles as, with the reason a park waits on.

    Each ``paused_*`` (and research) status an older release wrote is one park
    whose reason it names (``park_reason_of``).
    """
    raw_status = str(payload.get("status") or "").strip().lower()
    if raw_status == "done":
        return PlannerVerdictStatus.COMPLETED, None
    try:
        status = PlannerVerdictStatus(raw_status)
        return status, (str(payload.get("park_reason") or "") if status is _PARKED else None)
    except ValueError:
        if (park := park_reason_of(raw_status) or normalize_stop_kind(raw_status)) is not None:
            return _PARKED, park

    stop_kind = normalize_stop_kind(payload.get("stop_kind"))
    if stop_kind in {"budget_exhausted", "cost_unreconciled", "provider_cooldown"}:
        return _PARKED, stop_kind
    if stop_kind in {"backend_unavailable", "transient_error"}:
        return _PARKED, "infra"
    if stop_kind == "permanent_error" or payload.get("error"):
        return PlannerVerdictStatus.ERROR, None

    if research_result is not None and (
        research_result["correctness_status"] != "verified"
        or research_result["novelty_status"] == "unverified"
        or research_result["significance_status"] == "unverified"
    ):
        return _PARKED, "research_open"
    if payload.get("project_done") is True:
        return PlannerVerdictStatus.COMPLETED, None
    if int(
        payload.get("enqueued_tasks")
        or payload.get("tasks_added")
        or payload.get("task_count")
        or 0
    ) > 0:
        return PlannerVerdictStatus.PLANNED, None
    return _PARKED, "research_open"


def adapt_legacy_planner_verdict_event(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Map a persisted pre-contract planner verdict into the canonical shape."""
    raw_research_result = payload.get("research_result")
    if not isinstance(raw_research_result, dict):
        raw_research_result = payload.get("math_result")
    research_result = normalize_research_result(raw_research_result)
    status, park_reason = _legacy_status(payload, research_result=research_result)
    details = {
        key: value
        for key, value in payload.items()
        if key not in _PROTECTED_FIELDS
        and key not in {"math_result", "research_result", "event_validation"}
    }
    return build_planner_verdict_event(
        status=status,
        reason=str(payload.get("reason") or payload.get("summary") or status.value),
        project_id=str(payload.get("project_id") or ""),
        mission_id=str(payload.get("mission_id") or ""),
        research_target_level=normalize_research_target_level(
            payload.get("research_target_level")
        ),
        correctness_status=(
            research_result["correctness_status"] if research_result else None
        ),
        novelty_status=research_result["novelty_status"] if research_result else None,
        significance_status=(
            research_result["significance_status"] if research_result else None
        ),
        stop_kind=normalize_stop_kind(payload.get("stop_kind")),
        completion_kind=str(payload.get("completion_kind") or "") or None,
        park_reason=park_reason,
        **details,
    )


__all__ = [
    "PlannerVerdictStatus",
    "adapt_legacy_planner_verdict_event",
    "build_planner_verdict_event",
]
