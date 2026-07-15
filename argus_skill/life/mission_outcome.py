"""Normalize mission terminal statuses into stable outcome classes."""

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


__all__ = ["mission_outcome_class"]
