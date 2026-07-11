"""Canonical cross-component event names and envelope validation."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

EVENT_ENVELOPE_VERSION = 1
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class EventCategory(StrEnum):
    AGENT_IO = "agent_io"
    DAEMON = "daemon"
    IDEA = "idea"
    LIFECYCLE = "lifecycle"
    OPERATOR = "operator"
    PLANNER = "planner"
    PROVIDER = "provider"
    SKILL = "skill"
    USAGE = "usage"


class EventType(StrEnum):
    AGENT_IO_START = "agent.io.start"
    AGENT_IO_STREAM = "agent.io.stream"
    AGENT_IO_COMPLETE = "agent.io.complete"
    AGENT_IO_ERROR = "agent.io.error"
    USAGE_RECORDED = "usage.recorded"
    PROVIDER_REQUEST_STARTED = "provider.request.started"
    PROVIDER_REQUEST_COMPLETED = "provider.request.completed"
    PROVIDER_REQUEST_DENIED = "provider.request.denied"
    CODEX_UTIL_COMPLETED = "codex.util.completed"
    SKILL_COST_COMPLETED = "skill.cost.completed"
    BUDGET_RESERVATION_CREATED = "budget.reservation.created"
    BUDGET_RESERVATION_DENIED = "budget.reservation.denied"
    BUDGET_RESERVATION_SETTLED = "budget.reservation.settled"
    BUDGET_RESERVATION_RELEASED = "budget.reservation.released"
    BUDGET_UNPRICED_BLOCKED = "budget.unpriced.blocked"
    LOOP_START = "loop.start"
    LOOP_DONE = "loop.done"
    ROUND_START = "round.start"
    ROUND_MAIN_COMPLETED = "round.main.completed"
    ROUND_REVIEW_STARTED = "round.review.started"
    ROUND_REVIEW_COMPLETED = "round.review.completed"
    ROUND_ESCALATED = "round.escalated"
    ROUND_STALL = "round.stall"
    ROUND_REVIEWER_BACKEND_FAILURE = "round.reviewer_backend_failure"
    ENGINEER_PROGRESS = "engineer.progress"
    LIFE_STATUS = "life.status"
    LIFE_PHASE_STARTED = "life.phase.started"
    LIFE_MISSION_STARTED = "life.mission.started"
    LIFE_MISSION_COMPLETED = "life.mission.completed"
    LIFE_MISSION_FAILED = "life.mission.failed"
    LIFE_MISSION_SKIPPED = "life.mission.skipped"
    LIFE_MISSION_ORPHANED = "life.mission.orphaned"
    LIFE_MISSION_REQUEUED = "life.mission.requeued"
    LIFE_MANAGER_INTENT_STARTED = "life.manager.intent.started"
    LIFE_MANAGER_INTENT_COMPLETED = "life.manager.intent.completed"
    LIFE_MANAGER_INTENT_FAILED = "life.manager.intent.failed"
    LIFE_MANAGER_STAGE_DECISION = "life.manager.stage_decision"
    LIFE_VERTICAL_RESOLVED = "life.vertical.resolved"
    LIFE_PLANNER_START = "life.planner.start"
    LIFE_PLANNER_TASK_ADDED = "life.planner.task_added"
    LIFE_PLANNER_TASK_SKIPPED = "life.planner.task_skipped"
    LIFE_PLANNER_VERDICT = "life.planner.verdict"
    LIFE_PLANNER_WAITING = "life.planner.waiting"
    LIFE_PLANNER_TERMINAL_IDLE = "life.planner.terminal_idle"
    LIFE_PLANNER_VERIFICATION_PROBE = "life.planner.verification_probe"
    LIFE_PLANNER_STALL_ESCALATION = "life.planner.stall_escalation"
    LIFE_PLANNER_ERROR = "life.planner.error"
    LIFE_BUDGET_PAUSE = "life.budget.pause"
    LIFE_LIFECYCLE_BLOCK = "life.lifecycle.block"
    LIFE_LIFECYCLE_TRANSITION = "life.lifecycle.transition"
    LIFE_INBOX_QUEUED = "life.inbox.queued"
    LIFE_DAEMON_IDLE_TIMEOUT = "life.daemon.idle_timeout"
    DAEMON_PARKED = "daemon.parked"
    IDEA_SEARCH_STARTED = "idea.search.started"
    IDEA_SEARCH_COMPLETED = "idea.search.completed"
    IDEA_SEARCH_SKIPPED = "idea.search.skipped"
    SKILL_CREATED = "skill.created"
    SKILL_UPDATED = "skill.updated"
    SKILL_ARCHIVED = "skill.archived"
    OPERATOR_ALERT = "operator_alert"


LEGACY_EVENT_ALIASES: dict[str, EventType] = {
    "loop.started": EventType.LOOP_START,
    "loop.completed": EventType.LOOP_DONE,
    "round.started": EventType.ROUND_START,
    "mission.started": EventType.LIFE_MISSION_STARTED,
    "mission.completed": EventType.LIFE_MISSION_COMPLETED,
    "mission.error": EventType.LIFE_MISSION_FAILED,
}

SIGNAL_EVENT_TYPES: frozenset[str] = frozenset({
    EventType.LOOP_START,
    EventType.LOOP_DONE,
    EventType.ROUND_START,
    EventType.ROUND_MAIN_COMPLETED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.ROUND_ESCALATED,
    EventType.ROUND_STALL,
    EventType.ROUND_REVIEWER_BACKEND_FAILURE,
    EventType.SKILL_CREATED,
    EventType.SKILL_UPDATED,
    EventType.SKILL_ARCHIVED,
    EventType.LIFE_MISSION_STARTED,
    EventType.LIFE_MISSION_COMPLETED,
    EventType.LIFE_MANAGER_INTENT_STARTED,
    EventType.LIFE_MANAGER_INTENT_COMPLETED,
    EventType.LIFE_MANAGER_INTENT_FAILED,
    EventType.LIFE_MANAGER_STAGE_DECISION,
    EventType.LIFE_VERTICAL_RESOLVED,
    EventType.LIFE_PLANNER_START,
    EventType.LIFE_PLANNER_TASK_ADDED,
    EventType.LIFE_PLANNER_TASK_SKIPPED,
    EventType.LIFE_PLANNER_VERDICT,
    EventType.LIFE_PLANNER_WAITING,
    EventType.LIFE_PLANNER_TERMINAL_IDLE,
    EventType.LIFE_PLANNER_VERIFICATION_PROBE,
    EventType.LIFE_PLANNER_STALL_ESCALATION,
    EventType.LIFE_BUDGET_PAUSE,
    EventType.BUDGET_RESERVATION_DENIED,
    EventType.BUDGET_UNPRICED_BLOCKED,
    EventType.LIFE_LIFECYCLE_BLOCK,
    EventType.LIFE_LIFECYCLE_TRANSITION,
    EventType.PROVIDER_REQUEST_STARTED,
    EventType.PROVIDER_REQUEST_COMPLETED,
    EventType.PROVIDER_REQUEST_DENIED,
    EventType.LIFE_INBOX_QUEUED,
    EventType.LIFE_DAEMON_IDLE_TIMEOUT,
    EventType.DAEMON_PARKED,
    EventType.IDEA_SEARCH_STARTED,
    EventType.IDEA_SEARCH_COMPLETED,
    EventType.IDEA_SEARCH_SKIPPED,
    EventType.OPERATOR_ALERT,
})

CALL_SCOPED_EVENT_TYPES: frozenset[str] = frozenset({
    EventType.AGENT_IO_START,
    EventType.AGENT_IO_COMPLETE,
    EventType.AGENT_IO_ERROR,
    EventType.PROVIDER_REQUEST_STARTED,
    EventType.PROVIDER_REQUEST_COMPLETED,
    EventType.PROVIDER_REQUEST_DENIED,
    EventType.USAGE_RECORDED,
})

_REQUIRED_FIELDS: dict[EventType, tuple[str, ...]] = {
    EventType.AGENT_IO_START: ("call_id", "run_label"),
    EventType.AGENT_IO_STREAM: ("call_id", "stream", "line"),
    EventType.AGENT_IO_COMPLETE: ("call_id", "run_label"),
    EventType.AGENT_IO_ERROR: ("call_id", "error"),
    EventType.USAGE_RECORDED: ("call_id", "schema_version", "provider", "status"),
    EventType.PROVIDER_REQUEST_STARTED: ("provider", "run_label"),
    EventType.PROVIDER_REQUEST_COMPLETED: ("provider", "run_label"),
    EventType.PROVIDER_REQUEST_DENIED: ("provider", "run_label"),
    EventType.BUDGET_RESERVATION_CREATED: (
        "reservation_id",
        "call_id",
        "amount_usd",
    ),
    EventType.BUDGET_RESERVATION_DENIED: ("call_id", "reason"),
    EventType.BUDGET_RESERVATION_SETTLED: (
        "reservation_id",
        "call_id",
        "pricing_status",
    ),
    EventType.BUDGET_RESERVATION_RELEASED: ("reservation_id", "call_id"),
    EventType.BUDGET_UNPRICED_BLOCKED: ("call_id", "reason"),
}


@dataclass(frozen=True)
class EventSpec:
    type: EventType
    category: EventCategory
    signal: bool
    call_scoped: bool
    required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventValidation:
    valid: bool
    known: bool
    canonical_type: str
    errors: tuple[str, ...] = ()


def _category(event_type: EventType) -> EventCategory:
    value = event_type.value
    if value.startswith("agent.io.") or value == EventType.ENGINEER_PROGRESS:
        return EventCategory.AGENT_IO
    if value.startswith("provider."):
        return EventCategory.PROVIDER
    if value.startswith("usage.") or value.startswith("codex.util."):
        return EventCategory.USAGE
    if value.startswith("life.planner."):
        return EventCategory.PLANNER
    if value.startswith("skill."):
        return EventCategory.SKILL
    if value.startswith("idea."):
        return EventCategory.IDEA
    if value.startswith("daemon.") or value.startswith("life.daemon."):
        return EventCategory.DAEMON
    if value == EventType.OPERATOR_ALERT:
        return EventCategory.OPERATOR
    return EventCategory.LIFECYCLE


EVENT_SPECS: dict[EventType, EventSpec] = {
    event_type: EventSpec(
        type=event_type,
        category=_category(event_type),
        signal=event_type.value in SIGNAL_EVENT_TYPES,
        call_scoped=event_type.value in CALL_SCOPED_EVENT_TYPES,
        required_fields=_REQUIRED_FIELDS.get(event_type, ()),
    )
    for event_type in EventType
}


def canonical_event_type(value: Any) -> str:
    text = str(value or "").strip()
    alias = LEGACY_EVENT_ALIASES.get(text)
    return alias.value if alias is not None else text


def event_spec(value: Any) -> EventSpec | None:
    canonical = canonical_event_type(value)
    try:
        return EVENT_SPECS[EventType(canonical)]
    except (ValueError, KeyError):
        return None


def validate_event_envelope(
    event: Mapping[str, Any],
    *,
    require_known: bool = False,
) -> EventValidation:
    raw_type = str(event.get("type") or "").strip()
    canonical = canonical_event_type(raw_type)
    errors: list[str] = []
    if not raw_type:
        errors.append("type is required")
    elif EVENT_TYPE_RE.fullmatch(raw_type) is None:
        errors.append(f"invalid event type: {raw_type}")
    spec = event_spec(raw_type)
    if require_known and spec is None:
        errors.append(f"unknown event type: {raw_type}")
    if spec is not None:
        missing = [
            field
            for field in spec.required_fields
            if field not in event or event.get(field) is None
        ]
        if missing:
            errors.append(f"missing required fields: {', '.join(missing)}")
    ts = event.get("ts")
    if ts is not None and (isinstance(ts, bool) or not isinstance(ts, (int, float))):
        errors.append("ts must be numeric")
    version = event.get("event_schema_version")
    if version is not None and (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
    ):
        errors.append("event_schema_version must be a positive integer")
    return EventValidation(
        valid=not errors,
        known=spec is not None,
        canonical_type=canonical,
        errors=tuple(errors),
    )


def normalize_event_envelope(
    event: Mapping[str, Any] | Any,
    *,
    timestamp: float | None = None,
) -> dict[str, Any]:
    out = dict(event) if isinstance(event, Mapping) else {"raw": str(event)}
    out.pop("event_validation", None)
    out.pop("canonical_type", None)
    out.setdefault("ts", time.time() if timestamp is None else float(timestamp))
    out.setdefault("event_schema_version", EVENT_ENVELOPE_VERSION)
    validation = validate_event_envelope(out)
    raw_type = str(out.get("type") or "")
    if validation.canonical_type and validation.canonical_type != raw_type:
        out.setdefault("canonical_type", validation.canonical_type)
    if not validation.valid:
        out["event_validation"] = {
            "status": "invalid",
            "errors": list(validation.errors),
        }
    return out


def new_event(event_type: EventType | str, /, **payload: Any) -> dict[str, Any]:
    return normalize_event_envelope({**payload, "type": str(event_type)})


__all__ = [
    "CALL_SCOPED_EVENT_TYPES",
    "EVENT_ENVELOPE_VERSION",
    "EVENT_SPECS",
    "EVENT_TYPE_RE",
    "EventCategory",
    "EventSpec",
    "EventType",
    "EventValidation",
    "LEGACY_EVENT_ALIASES",
    "SIGNAL_EVENT_TYPES",
    "canonical_event_type",
    "event_spec",
    "new_event",
    "normalize_event_envelope",
    "validate_event_envelope",
]
