"""Normalize mission completion without collapsing independent meanings."""

from __future__ import annotations

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
#: Reviewer verdicts asserting the work itself is unfinished rather than
#: settled. See ``review_keeps_mission_resumable``.
_RESUMING_REVIEW_STATUSES = frozenset({"continue"})


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


def review_keeps_mission_resumable(
    *,
    status: str,
    success: bool,
    review_status: str,
    stop_kind: object = None,
) -> bool:
    """Return whether a Reviewer verdict outranks a stall status.

    ``no_progress`` and ``max_rounds`` are facts about *this mission's rounds*:
    a round added nothing, or the round budget ran out. ``continue`` is the
    Reviewer's finding about *the work*: it is unfinished and should go on.
    These are independent, and letting the round-level fact win discards the
    verdict — the project then records a terminal stall for a goal a Reviewer
    explicitly asked to continue, and the task signature is quarantined out of
    replanning, so the run sits idle with an unfinished objective and an empty
    queue.

    Deliberately narrow. A success needs no resumption, and ``blocked``,
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
    return str(review_status or "").strip().lower() in _RESUMING_REVIEW_STATUSES


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
) -> dict[str, object]:
    """Build the canonical terminal outcome from structured owners."""
    normalized_status = str(status or "").strip().lower()
    normalized_stop = str(stop_kind or "").strip().lower()
    outcome_class = mission_outcome_class(normalized_status, bool(success))

    normalized_review = str(review_status or "").strip().lower()
    if normalized_review not in _REVIEW_STATUSES:
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

    if normalized_status.startswith("paused_") or resumable:
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
    review = str(outcome.get("review_status") or "").strip().lower()
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
            "continue": "Review requested another pass",
            "blocked": "Review needs an external decision",
            "research_incomplete": "Review found work remaining",
            "paused_no_breakthrough": "Review found no breakthrough yet",
            "exhausted_current_methods": "Review found the current methods exhausted",
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
