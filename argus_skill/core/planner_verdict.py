"""Canonical contract for ``life.planner.verdict`` events."""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from .event_catalog import EventType, validate_event_envelope
from .research_contract import (
    normalize_research_result,
    normalize_research_target_level,
)
from .stop_kinds import normalize_stop_kind


class PlannerVerdictStatus(str, Enum):
    PLANNED = "planned"
    COMPLETED = "completed"
    RESEARCH_INCOMPLETE = "research_incomplete"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_NO_BREAKTHROUGH = "paused_no_breakthrough"
    EXHAUSTED_CURRENT_METHODS = "exhausted_current_methods"
    PROVIDER_COOLDOWN = "provider_cooldown"
    PROVIDER_FENCE = "provider_fence"
    INFRA_BLOCKED = "infra_blocked"
    ERROR = "error"


_STATUS_POLICY: dict[PlannerVerdictStatus, tuple[bool, bool]] = {
    PlannerVerdictStatus.PLANNED: (True, False),
    PlannerVerdictStatus.COMPLETED: (True, False),
    PlannerVerdictStatus.RESEARCH_INCOMPLETE: (False, True),
    PlannerVerdictStatus.PAUSED_BUDGET: (False, True),
    PlannerVerdictStatus.PAUSED_NO_BREAKTHROUGH: (False, True),
    PlannerVerdictStatus.EXHAUSTED_CURRENT_METHODS: (False, True),
    PlannerVerdictStatus.PROVIDER_COOLDOWN: (False, True),
    PlannerVerdictStatus.PROVIDER_FENCE: (False, True),
    PlannerVerdictStatus.INFRA_BLOCKED: (False, True),
    PlannerVerdictStatus.ERROR: (False, False),
}

_LEGACY_STATUS_ALIASES = {
    "done": PlannerVerdictStatus.COMPLETED,
    "paused_provider_cooldown": PlannerVerdictStatus.PROVIDER_COOLDOWN,
    "paused_provider_fence": PlannerVerdictStatus.PROVIDER_FENCE,
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
        **details,
    }
    event["tasks_added"] = int(event.get("enqueued_tasks", 0) or 0)
    validation = validate_event_envelope(event, require_known=True)
    if not validation.valid:
        raise ValueError(
            "invalid life.planner.verdict: " + "; ".join(validation.errors)
        )
    return event


def _legacy_status(
    payload: Mapping[str, Any],
    *,
    research_result: dict[str, Any] | None,
) -> PlannerVerdictStatus:
    raw_status = str(payload.get("status") or "").strip().lower()
    try:
        return PlannerVerdictStatus(raw_status)
    except ValueError:
        if raw_status in _LEGACY_STATUS_ALIASES:
            return _LEGACY_STATUS_ALIASES[raw_status]

    stop_kind = normalize_stop_kind(payload.get("stop_kind"))
    if stop_kind == "budget_exhausted":
        return PlannerVerdictStatus.PAUSED_BUDGET
    if stop_kind == "provider_cooldown":
        return PlannerVerdictStatus.PROVIDER_COOLDOWN
    if stop_kind == "provider_fence":
        return PlannerVerdictStatus.PROVIDER_FENCE
    if stop_kind in {"backend_unavailable", "transient_error"}:
        return PlannerVerdictStatus.INFRA_BLOCKED
    if stop_kind == "permanent_error" or payload.get("error"):
        return PlannerVerdictStatus.ERROR

    if research_result is not None and (
        research_result["correctness_status"] != "verified"
        or research_result["novelty_status"] == "unverified"
        or research_result["significance_status"] == "unverified"
    ):
        return PlannerVerdictStatus.RESEARCH_INCOMPLETE
    if payload.get("project_done") is True:
        return PlannerVerdictStatus.COMPLETED
    if int(
        payload.get("enqueued_tasks")
        or payload.get("tasks_added")
        or payload.get("task_count")
        or 0
    ) > 0:
        return PlannerVerdictStatus.PLANNED
    return PlannerVerdictStatus.RESEARCH_INCOMPLETE


def adapt_legacy_planner_verdict_event(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Map a persisted pre-contract planner verdict into the canonical shape."""
    raw_research_result = payload.get("research_result")
    if not isinstance(raw_research_result, dict):
        raw_research_result = payload.get("math_result")
    research_result = normalize_research_result(raw_research_result)
    status = _legacy_status(payload, research_result=research_result)
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
        **details,
    )


__all__ = [
    "PlannerVerdictStatus",
    "adapt_legacy_planner_verdict_event",
    "build_planner_verdict_event",
]
