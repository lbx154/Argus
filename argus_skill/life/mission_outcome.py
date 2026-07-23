"""Normalize mission completion without collapsing independent meanings."""

from __future__ import annotations

from ..core.research_direction import normalize_research_direction

_COMPLETED_STATUSES = frozenset({"done", "success", "completed"})
_INCOMPLETE_STATUSES = frozenset({
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
})
_STALLED_STATUSES = frozenset({"no_progress", "max_rounds"})
_BLOCKED_STATUSES = frozenset({"blocked", "infra_blocked"})
_FAILED_STATUSES = frozenset({"error", "failed", "supervisor_error"})
_REVIEW_STATUSES = frozenset({
    "done",
    "continue",
    "blocked",
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
})
_FAILURE_SOURCES = frozenset({
    "validator_defect",
    "provenance_binding_defect",
    "infrastructure_failure",
    "scientific_evidence_failure",
})
_FAILURE_LAYERS = frozenset({
    "platform",
    "orchestration",
    "evaluator",
    "evidence_packaging",
    "scientific",
    "operator",
    "unknown",
})


def mission_outcome_class(status: str, success: bool) -> str:
    """Map raw mission status flags to the lifecycle outcome buckets."""

    normalized = str(status or "").strip().lower()
    if success or normalized in _COMPLETED_STATUSES:
        return "completed"
    if normalized in _INCOMPLETE_STATUSES:
        return "incomplete"
    if normalized in _STALLED_STATUSES:
        return "stalled"
    if normalized in _BLOCKED_STATUSES:
        return "blocked"
    if normalized in _FAILED_STATUSES:
        return "failed"
    return "ended"


def mission_outcome_dimensions(
    *,
    status: str,
    success: bool,
    review_status: str = "",
    stage_transition: object = None,
    scientific_decision: str = "",
    failure_source: str = "",
    failure_layer: str = "",
    stop_kind: object = None,
    resumable: bool = False,
) -> dict[str, object]:
    """Build the canonical terminal outcome from structured owners."""
    normalized_status = str(status or "").strip().lower()
    normalized_stop = str(stop_kind or "").strip().lower()
    outcome_class = mission_outcome_class(normalized_status, bool(success))
    if normalized_status.startswith("paused_") or bool(resumable):
        execution_status = "paused"
    elif normalized_status == "aborted" or normalized_stop == "operator_abort":
        execution_status = "aborted"
    else:
        execution_status = outcome_class

    normalized_review = str(review_status or "").strip().lower()
    if normalized_review not in _REVIEW_STATUSES:
        normalized_review = "not_assessed"

    stage_action = ""
    if isinstance(stage_transition, dict):
        stage_action = str(stage_transition.get("action") or "").strip().lower()
    stage_certification = {
        "advance": "certified",
        "complete": "certified",
        "hold": "not_certified",
        "rollback": "revoked",
    }.get(stage_action, "not_assessed")

    normalized_science = normalize_research_direction(
        scientific_decision,
        default="uncertain",
    )
    normalized_failure = str(failure_source or "").strip().lower()
    if normalized_failure not in _FAILURE_SOURCES:
        normalized_failure = ""
    normalized_failure_layer = str(failure_layer or "").strip().lower()
    if normalized_failure_layer not in _FAILURE_LAYERS:
        normalized_failure_layer = ""

    return {
        "execution_status": execution_status,
        "review_status": normalized_review,
        "stage_certification": stage_certification,
        "scientific_decision": normalized_science,
        "failure_source": normalized_failure,
        "failure_layer": normalized_failure_layer,
        "interruption_kind": normalized_stop or "none",
        "resumable": bool(resumable),
    }


def outcome_dimension_summary(outcome: object) -> list[str]:
    """Render the compact cross-surface projection of a canonical outcome."""
    if not isinstance(outcome, dict):
        return []
    execution = str(outcome.get("execution_status") or "").strip().lower()
    if not execution:
        return []
    review = str(outcome.get("review_status") or "").strip().lower()
    stage = str(outcome.get("stage_certification") or "").strip().lower()
    science = normalize_research_direction(outcome.get("scientific_decision"))
    failure = str(outcome.get("failure_source") or "").strip().lower()
    failure_layer = str(outcome.get("failure_layer") or "").strip().lower()
    interruption = str(outcome.get("interruption_kind") or "").strip().lower()
    parts = [f"execution={execution}"]
    if review and review != "not_assessed":
        parts.append(f"review={review}")
    if stage and stage != "not_assessed":
        parts.append(f"stage={stage}")
    if science and science != "uncertain":
        parts.append(f"science={science}")
    if failure:
        parts.append(f"failure={failure}")
    if failure_layer:
        parts.append(f"layer={failure_layer}")
    if interruption and interruption != "none":
        parts.append(f"interrupt={interruption}")
    if outcome.get("resumable") is True:
        parts.append("resumable=yes")
    return parts


__all__ = [
    "mission_outcome_class",
    "mission_outcome_dimensions",
    "outcome_dimension_summary",
]
