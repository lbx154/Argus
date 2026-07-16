"""Canonical cross-component event names and envelope validation."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

EVENT_ENVELOPE_VERSION = 1
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_PAYLOAD_SCHEMA_PATH = Path(__file__).with_name("event_payload_schemas.json")


def _load_payload_schemas() -> tuple[int, dict[str, dict[str, Any]]]:
    payload = json.loads(_PAYLOAD_SCHEMA_PATH.read_text(encoding="utf-8"))
    return int(payload["schema_version"]), dict(payload["events"])


EVENT_PAYLOAD_SCHEMA_VERSION, EVENT_PAYLOAD_SCHEMAS = _load_payload_schemas()


class EventCategory(StrEnum):
    AGENT_IO = "agent_io"
    DAEMON = "daemon"
    IDEA = "idea"
    LIFECYCLE = "lifecycle"
    OPERATOR = "operator"
    PLANNER = "planner"
    PROVIDER = "provider"
    RESEARCH = "research"
    SKILL = "skill"
    USAGE = "usage"
    WIKI = "wiki"


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
    BUDGET_FENCE_BREACH_BLOCKED = "budget.fence_breach.blocked"
    LOOP_START = "loop.start"
    LOOP_DONE = "loop.done"
    ROUND_START = "round.start"
    ROUND_MAIN_COMPLETED = "round.main.completed"
    ROUND_REVIEW_STARTED = "round.review.started"
    ROUND_REVIEW_DEFERRED = "round.review.deferred"
    ROUND_REVIEW_COMPLETED = "round.review.completed"
    ROUND_SECRET_REDACTED = "round.secret_redacted"
    ROUND_ESCALATED = "round.escalated"
    ROUND_STALL = "round.stall"
    ROUND_REVIEWER_BACKEND_FAILURE = "round.reviewer_backend_failure"
    ENGINEER_PROGRESS = "engineer.progress"
    ENGINEER_SELF_REVIEW_ACCEPTED = "engineer.self_review.accepted"
    ENGINEER_SELF_REVIEW_REJECTED = "engineer.self_review.rejected"
    ENGINEER_SKILL_MAINTENANCE_STARTED = "engineer.skill_maintenance.started"
    ENGINEER_SKILL_MAINTENANCE_COMPLETED = "engineer.skill_maintenance.completed"
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
    LIFE_PLAN_SIGNAL = "life.plan.signal"
    LIFE_PLAN_REVISION_PROPOSED = "life.plan.revision.proposed"
    LIFE_PLAN_REVISION_REJECTED = "life.plan.revision.rejected"
    LIFE_PLAN_REVISION_COMMITTED = "life.plan.revision.committed"
    LIFE_PLAN_NODE_SUPERSEDED = "life.plan.node.superseded"
    LIFE_BUDGET_PAUSE = "life.budget.pause"
    LIFE_LIFECYCLE_BLOCK = "life.lifecycle.block"
    LIFE_LIFECYCLE_TRANSITION = "life.lifecycle.transition"
    LIFE_INBOX_QUEUED = "life.inbox.queued"
    LIFE_INBOX_DRAINED = "life.inbox.drained"
    LIFE_DAEMON_IDLE_TIMEOUT = "life.daemon.idle_timeout"
    DAEMON_PARKED = "daemon.parked"
    DAEMON_COMMAND_SUBMITTED = "daemon.command.submitted"
    DAEMON_COMMAND_COMPLETED = "daemon.command.completed"
    DAEMON_COMMAND_REJECTED = "daemon.command.rejected"
    IDEA_SEARCH_STARTED = "idea.search.started"
    IDEA_SEARCH_COMPLETED = "idea.search.completed"
    IDEA_SEARCH_SKIPPED = "idea.search.skipped"
    RESEARCH_HYPOTHESIS_PROPOSED = "research.hypothesis.proposed"
    RESEARCH_EXPERIMENT_STARTED = "research.experiment.started"
    RESEARCH_EXPERIMENT_COMPLETED = "research.experiment.completed"
    RESEARCH_METRIC_REPORTED = "research.metric.reported"
    RESEARCH_METRIC_VERIFIED = "research.metric.verified"
    RESEARCH_ARTIFACT_REGISTERED = "research.artifact.registered"
    RESEARCH_ACHIEVEMENT_CERTIFIED = "research.achievement.certified"
    SKILL_CREATED = "skill.created"
    SKILL_UPDATED = "skill.updated"
    SKILL_ARCHIVED = "skill.archived"
    SKILL_OUTCOME = "skill.outcome"
    SKILL_SCIENTIST_STARTED = "skill.scientist.started"
    SKILL_SCIENTIST_CREATED = "skill.scientist.created"
    SKILL_SCIENTIST_ADAPTATION_STARTED = "skill.scientist.adaptation_started"
    SKILL_SCIENTIST_ADAPTATION_CREATED = "skill.scientist.adaptation_created"
    SKILL_TIDIED = "skill.tidied"
    SKILL_COMPACTED = "skill.compacted"
    SKILL_COMPACT_ERROR = "skill.compact.error"
    SKILL_OP_ERROR = "skill.op.error"
    SKILL_OP_REFUSED = "skill.op.refused"
    SKILL_PROPOSAL_REJECTED = "skill.proposal.rejected"
    SKILL_DISTILL_REJECTED = "skill.distill.rejected"
    SKILL_REVISED = "skill.revised"
    SKILL_USE_RECORDED = "skill.use.recorded"
    SKILL_HISTORY_COMPRESSED = "skill.history.compressed"
    SKILL_EVOLUTION_COMPLETED = "skill.evolution.completed"
    WIKI_INITIALIZED = "wiki.initialized"
    WIKI_INITIALIZATION_FAILED = "wiki.initialization.failed"
    WIKI_HOOK_OK = "wiki.hook.ok"
    WIKI_HOOK_WARNING = "wiki.hook.warning"
    WIKI_COMPACTED = "wiki.compacted"
    WIKI_COMPACT_ERROR = "wiki.compact.error"
    WIKI_OP_ERROR = "wiki.op.error"
    WIKI_OP_REJECTED = "wiki.op.rejected"
    WIKI_CREATED = "wiki.created"
    WIKI_UPDATED = "wiki.updated"
    WIKI_RETIRED = "wiki.retired"
    WIKI_SOURCE_CREATED = "wiki.source.created"
    WIKI_SOURCE_SKIPPED = "wiki.source.skipped"
    WIKI_PROMOTION_PROMOTED = "wiki.promotion.promoted"
    WIKI_PROMOTION_DEMOTED = "wiki.promotion.demoted"
    WIKI_RETIRED_COMPRESSED = "wiki.retired.compressed"
    WIKI_EVOLUTION_COMPLETED = "wiki.evolution.completed"
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
    EventType.ROUND_REVIEW_DEFERRED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.ROUND_SECRET_REDACTED,
    EventType.ROUND_ESCALATED,
    EventType.ROUND_STALL,
    EventType.ROUND_REVIEWER_BACKEND_FAILURE,
    EventType.ENGINEER_SELF_REVIEW_ACCEPTED,
    EventType.ENGINEER_SELF_REVIEW_REJECTED,
    EventType.ENGINEER_SKILL_MAINTENANCE_STARTED,
    EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED,
    EventType.SKILL_CREATED,
    EventType.SKILL_UPDATED,
    EventType.SKILL_ARCHIVED,
    EventType.SKILL_OUTCOME,
    EventType.SKILL_SCIENTIST_STARTED,
    EventType.SKILL_SCIENTIST_CREATED,
    EventType.SKILL_SCIENTIST_ADAPTATION_STARTED,
    EventType.SKILL_SCIENTIST_ADAPTATION_CREATED,
    EventType.SKILL_TIDIED,
    EventType.SKILL_COMPACTED,
    EventType.SKILL_COMPACT_ERROR,
    EventType.SKILL_OP_ERROR,
    EventType.SKILL_OP_REFUSED,
    EventType.SKILL_PROPOSAL_REJECTED,
    EventType.SKILL_DISTILL_REJECTED,
    EventType.SKILL_REVISED,
    EventType.SKILL_USE_RECORDED,
    EventType.SKILL_HISTORY_COMPRESSED,
    EventType.SKILL_EVOLUTION_COMPLETED,
    EventType.WIKI_INITIALIZED,
    EventType.WIKI_INITIALIZATION_FAILED,
    EventType.WIKI_HOOK_OK,
    EventType.WIKI_HOOK_WARNING,
    EventType.WIKI_COMPACTED,
    EventType.WIKI_COMPACT_ERROR,
    EventType.WIKI_OP_ERROR,
    EventType.WIKI_OP_REJECTED,
    EventType.WIKI_CREATED,
    EventType.WIKI_UPDATED,
    EventType.WIKI_RETIRED,
    EventType.WIKI_SOURCE_CREATED,
    EventType.WIKI_SOURCE_SKIPPED,
    EventType.WIKI_PROMOTION_PROMOTED,
    EventType.WIKI_PROMOTION_DEMOTED,
    EventType.WIKI_RETIRED_COMPRESSED,
    EventType.WIKI_EVOLUTION_COMPLETED,
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
    EventType.LIFE_PLAN_SIGNAL,
    EventType.LIFE_PLAN_REVISION_PROPOSED,
    EventType.LIFE_PLAN_REVISION_REJECTED,
    EventType.LIFE_PLAN_REVISION_COMMITTED,
    EventType.LIFE_PLAN_NODE_SUPERSEDED,
    EventType.LIFE_BUDGET_PAUSE,
    EventType.BUDGET_RESERVATION_DENIED,
    EventType.BUDGET_UNPRICED_BLOCKED,
    EventType.BUDGET_FENCE_BREACH_BLOCKED,
    EventType.LIFE_LIFECYCLE_BLOCK,
    EventType.LIFE_LIFECYCLE_TRANSITION,
    EventType.PROVIDER_REQUEST_STARTED,
    EventType.PROVIDER_REQUEST_COMPLETED,
    EventType.PROVIDER_REQUEST_DENIED,
    EventType.LIFE_INBOX_QUEUED,
    EventType.LIFE_INBOX_DRAINED,
    EventType.LIFE_DAEMON_IDLE_TIMEOUT,
    EventType.DAEMON_PARKED,
    EventType.DAEMON_COMMAND_COMPLETED,
    EventType.DAEMON_COMMAND_REJECTED,
    EventType.IDEA_SEARCH_STARTED,
    EventType.IDEA_SEARCH_COMPLETED,
    EventType.IDEA_SEARCH_SKIPPED,
    EventType.RESEARCH_HYPOTHESIS_PROPOSED,
    EventType.RESEARCH_EXPERIMENT_STARTED,
    EventType.RESEARCH_EXPERIMENT_COMPLETED,
    EventType.RESEARCH_METRIC_REPORTED,
    EventType.RESEARCH_METRIC_VERIFIED,
    EventType.RESEARCH_ARTIFACT_REGISTERED,
    EventType.RESEARCH_ACHIEVEMENT_CERTIFIED,
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
    if value.startswith("agent.io.") or value.startswith("engineer."):
        return EventCategory.AGENT_IO
    if value.startswith("provider."):
        return EventCategory.PROVIDER
    if value.startswith("usage.") or value.startswith("codex.util."):
        return EventCategory.USAGE
    if value.startswith("life.planner."):
        return EventCategory.PLANNER
    if value.startswith("skill."):
        return EventCategory.SKILL
    if value.startswith("wiki."):
        return EventCategory.WIKI
    if value.startswith("idea."):
        return EventCategory.IDEA
    if value.startswith("research."):
        return EventCategory.RESEARCH
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
        required_fields=tuple(
            EVENT_PAYLOAD_SCHEMAS.get(event_type.value, {}).get("required") or ()
        ),
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


def event_payload_schema(value: Any) -> dict[str, Any] | None:
    return EVENT_PAYLOAD_SCHEMAS.get(canonical_event_type(value))


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    return True


def _validate_payload(event: Mapping[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = int(schema.get("version") or 1)
    recorded_version = event.get("payload_schema_version")
    if recorded_version is not None and recorded_version != version:
        errors.append(
            f"payload_schema_version must be {version}; got {recorded_version!r}"
        )
    for field, field_schema in (schema.get("properties") or {}).items():
        if field not in event:
            continue
        value = event[field]
        expected = field_schema.get("type")
        expected_types = expected if isinstance(expected, list) else [expected]
        expected_types = [item for item in expected_types if isinstance(item, str)]
        if expected_types and not any(
            _matches_json_type(value, item) for item in expected_types
        ):
            errors.append(f"field {field} must be {' or '.join(expected_types)}")
            continue
        if "const" in field_schema and value != field_schema["const"]:
            errors.append(f"field {field} must equal {field_schema['const']!r}")
        allowed = field_schema.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            errors.append(f"field {field} must be one of {allowed!r}")
        if isinstance(value, str) and "minLength" in field_schema:
            if len(value) < int(field_schema["minLength"]):
                errors.append(
                    f"field {field} must have length >= {field_schema['minLength']}"
                )
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and "minimum" in field_schema
            and value < float(field_schema["minimum"])
        ):
            errors.append(f"field {field} must be >= {field_schema['minimum']}")
        if isinstance(value, list) and isinstance(field_schema.get("items"), dict):
            item_type = field_schema["items"].get("type")
            if isinstance(item_type, str) and any(
                not _matches_json_type(item, item_type) for item in value
            ):
                errors.append(f"field {field} items must be {item_type}")
    return errors


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
    payload_schema = event_payload_schema(raw_type)
    if payload_schema is not None:
        errors.extend(_validate_payload(event, payload_schema))
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
    payload_schema = event_payload_schema(out.get("type"))
    if payload_schema is not None:
        out.setdefault(
            "payload_schema_version",
            int(payload_schema.get("version") or 1),
        )
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
    "EVENT_PAYLOAD_SCHEMA_VERSION",
    "EVENT_PAYLOAD_SCHEMAS",
    "EVENT_SPECS",
    "EVENT_TYPE_RE",
    "EventCategory",
    "EventSpec",
    "EventType",
    "EventValidation",
    "LEGACY_EVENT_ALIASES",
    "SIGNAL_EVENT_TYPES",
    "canonical_event_type",
    "event_payload_schema",
    "event_spec",
    "new_event",
    "normalize_event_envelope",
    "validate_event_envelope",
]
