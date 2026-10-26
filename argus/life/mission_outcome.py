"""Normalize mission completion without collapsing independent meanings."""

from __future__ import annotations

from ..core.handoff import holder_of
from ..core.stop_kinds import is_pause_status, park_reason_of

_COMPLETED_STATUSES = frozenset({"done", "success", "completed"})
_STALLED_STATUSES = frozenset({"no_progress", "max_rounds"})
_FAILED_STATUSES = frozenset({"error", "failed", "supervisor_error"})
#: The holders a review names; ``engineer`` asserts the work itself is
#: unfinished rather than settled (``review_keeps_mission_resumable``).
_REVIEW_HOLDERS = frozenset({"done", "engineer", "planner", "manager", "operator"})


def mission_outcome_class(status: str, success: bool, park_reason: object = "") -> str:
    """Map raw mission status flags to the lifecycle outcome buckets.

    ``park_reason`` is the reason a ``parked`` status carries; an open research
    question leaves the work incomplete and a failed host blocks it.
    """

    normalized = str(status or "").strip().lower()
    park = park_reason_of(normalized, park_reason)
    if normalized == "stage_continues":
        return "incomplete"
    if success or normalized in _COMPLETED_STATUSES:
        return "completed"
    if park == "research_open":
        return "incomplete"
    if normalized in _STALLED_STATUSES:
        return "stalled"
    if normalized in {"manager", "operator"}:
        # Handed to the Manager or the person: it waits for them, never fails.
        return "incomplete"
    if normalized == "blocked" or park == "infra":
        return "blocked"
    if normalized in _FAILED_STATUSES:
        return "failed"
    return "ended"


def review_keeps_mission_resumable(
    *,
    status: str,
    success: bool,
    review_status: str,
    stop_kind: object = None,
) -> bool:
    """Return whether a Reviewer verdict outranks a stall status.

    ``no_progress`` and ``max_rounds`` are facts about *this mission's rounds*:
    a round added nothing, or the round budget ran out. ``→ engineer`` is the
    Reviewer's finding about *the work*: it is unfinished and should go on.
    These are independent, and letting the round-level fact win discards the
    verdict — the project then records a terminal stall for a goal a Reviewer
    explicitly asked to continue.

    Deliberately narrow. A success needs no resumption, and a handoff past the team,
    ``failed`` and an operator abort stop for reasons no review verdict may
    override: a pending operator question, a crash, and an explicit stop.
    """
    if success:
        return False
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "aborted":
        return False
    if str(stop_kind or "").strip().lower() == "operator_abort":
        return False
    if mission_outcome_class(normalized_status, False) != "stalled":
        return False
    return holder_of(review_status) == "engineer"


def mission_outcome_dimensions(
    *,
    status: str,
    success: bool,
    review_status: str = "",
    stage_transition: object = None,
    stage_transition_skipped: bool = False,
    stage_transition_deferred: bool = False,
    stop_kind: object = None,
    resumable: bool = False,
    park_reason: object = "",
) -> dict[str, object]:
    """Build the canonical terminal outcome from structured owners."""
    normalized_status = str(status or "").strip().lower()
    normalized_stop = str(stop_kind or "").strip().lower()
    outcome_class = mission_outcome_class(normalized_status, bool(success), park_reason)

    normalized_review = holder_of(review_status)
    if normalized_review not in _REVIEW_HOLDERS:
        normalized_review = "not_assessed"

    # Applied here rather than only at the settlement call site: this function
    # is also the projection that rebuilds a mission view from its recorded
    # event, where the only ``resumable`` available is whatever was written.
    resumable = bool(resumable) or review_keeps_mission_resumable(
        status=normalized_status,
        success=bool(success),
        review_status=normalized_review,
        stop_kind=normalized_stop,
    )

    if is_pause_status(normalized_status) or resumable:
        execution_status = "paused"
    elif normalized_status == "aborted" or normalized_stop == "operator_abort":
        execution_status = "aborted"
    else:
        execution_status = outcome_class

    stage_action = ""
    if isinstance(stage_transition, dict):
        stage_action = str(stage_transition.get("action") or "").strip().lower()
    if stage_transition_skipped:
        stage_certification = "intentionally_skipped"
    elif stage_action:
        stage_certification = {
            "advance": "certified",
            "complete": "certified",
            "hold": "not_certified",
            "rollback": "revoked",
        }.get(stage_action, "not_assessed")
    elif stage_transition_deferred:
        # A Planner-authored intermediate node deliberately holds the stage:
        # its reviewed evidence is real, it simply has not been adjudicated
        # yet. Kept distinct from ``intentionally_skipped``, which means a
        # review-only workflow suppressed the stage writer and so must never
        # be replayed. Campaign-level stage reconciliation replays deferred
        # evidence; without this distinction every Planner node looked like a
        # deliberate suppression and no stage could ever close.
        stage_certification = "deferred"
    else:
        stage_certification = "not_assessed"

    return {
        "execution_status": execution_status,
        "review_status": normalized_review,
        "stage_certification": stage_certification,
        "interruption_kind": normalized_stop or "none",
        "resumable": bool(resumable),
    }


def outcome_dimension_summary(outcome: object) -> list[str]:
    """Render canonical outcome dimensions as compact colleague-readable facts."""
    if not isinstance(outcome, dict):
        return []
    execution = str(outcome.get("execution_status") or "").strip().lower()
    if not execution:
        return []
    review = holder_of(outcome.get("review_status"))
    stage = str(outcome.get("stage_certification") or "").strip().lower()
    interruption = str(outcome.get("interruption_kind") or "").strip().lower()
    parts = [{
        "completed": "Work completed",
        "paused": "Work paused",
        "aborted": "Work canceled",
        "incomplete": "Work remains",
        "blocked": "Work is blocked",
        "stalled": "Work stalled",
        "failed": "Work failed",
        "ended": "Work ended",
    }.get(execution, f"Work {execution.replace('_', ' ')}")]
    if review and review != "not_assessed":
        parts.append({
            "done": "Review passed",
            "engineer": "Review requested another pass",
            "planner": "Review asked for a new plan",
            "manager": "Review needs an external decision",
            "operator": "Review leaves a decision to you",
        }.get(review, f"Review {review.replace('_', ' ')}"))
    if stage and stage != "not_assessed":
        parts.append({
            "certified": "Stage certified",
            "not_certified": "Stage remains open",
            "revoked": "Stage certification revoked",
            "intentionally_skipped": "Stage review intentionally skipped",
            "deferred": "Stage decision deferred",
        }.get(stage, f"Stage {stage.replace('_', ' ')}"))
    if interruption and interruption != "none":
        parts.append({
            "operator_abort": "Stopped at your request",
            "budget_exhausted": "Stopped at the budget limit",
            "cost_unreconciled": "Waiting for provider usage reconciliation (not a budget limit)",
        }.get(interruption, f"Stopped because {interruption.replace('_', ' ')}"))
    if outcome.get("resumable") is True:
        parts.append("Can resume")
    return parts


__all__ = [
    "mission_outcome_class",
    "mission_outcome_dimensions",
    "outcome_dimension_summary",
    "review_keeps_mission_resumable",
]
