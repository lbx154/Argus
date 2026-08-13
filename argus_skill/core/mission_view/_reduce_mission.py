"""Mission-lifecycle and round/engineer mission-view event-family reducers.

Covers the start/complete/fail of a mission plus the per-round Engineer/
Reviewer handoff sequence. This module only projects structured event fields
into the read model; it never re-derives Reviewer verdicts or Manager stage
authority — those decisions already happened upstream and are carried
verbatim on the event payload.
"""
from __future__ import annotations

from typing import Any, Mapping

from ...life.mission_outcome import mission_outcome_class, mission_outcome_dimensions
from ..event_catalog import EventType
from ..role_reply import strip_named_lines
from ._reduce_helpers import (
    _PROGRESS_LABELS,
    _integer,
    _role_work,
    _set_role,
    _text,
    _timeline,
    _visible_role_work_progress,
)

_MISSION_OUTCOME_PRESENTATIONS = {
    "completed": ("complete", "done", "Task completed", "success"),
    "incomplete": ("incomplete", "done", "Work remains", "info"),
    "stalled": ("stalled", "done", "No useful progress", "info"),
    "blocked": ("blocked", "error", "Cannot continue yet", "error"),
    "failed": ("failed", "error", "Task failed", "error"),
    "ended": ("ended", "done", "Mission ended", "info"),
}


def _mission_outcome_presentation(
    event: Mapping[str, Any],
    event_type: str,
) -> tuple[str, str, str, str]:
    if event_type == EventType.LIFE_MISSION_FAILED:
        outcome_class = "failed"
    else:
        candidate = _text(event, "outcome_class").lower()
        outcome_class = (
            candidate
            if candidate in _MISSION_OUTCOME_PRESENTATIONS
            else mission_outcome_class(
                status=_text(event, "status"),
                success=bool(event.get("success")),
            )
        )
    mission_status, role_status, label, tone = _MISSION_OUTCOME_PRESENTATIONS[
        outcome_class
    ]
    if (
        outcome_class == "completed"
        and event.get("final_submission_certified") is True
    ):
        label = "Submission certified"
    elif outcome_class == "ended":
        raw_status = _text(event, "status")
        if raw_status:
            label = f"{label} · {raw_status}"
    return mission_status, role_status, label, tone


def reduce_mission_lifecycle_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type == EventType.LIFE_MISSION_STARTED:
        if not mission.get("campaign_started_at"):
            mission["campaign_started_at"] = ts
        mission.update({
            "id": _text(event, "item_id"),
            "title": _text(event, "title", 240),
            "objective": _text(event, "objective", 2000),
            "summary": "",
            "final_output": "",
            "status": "working",
            "started_at": ts,
            "completed_at": None,
        })
        # Review state is mission-scoped.  Without an explicit reset, a newly
        # started mission inherits the previous mission's accepted/rejected
        # verdict in mission-view.json until its first review finishes.  The
        # execution loop does not use that stale projection for adjudication,
        # but operators and supervision tooling must not mistake it for current
        # evidence.
        view["review"] = {"status": "", "reason": "", "rejected_attempts": 0}
        view["outcome"] = {}
        _set_role(view, "reviewer", "waiting", "Awaiting engineer handoff", ts)
        _set_role(view, "engineer", "active", "Starting mission", ts)
        _timeline(view, event, role="engineer", title="Mission started", detail=_text(event, "title"), tone="info")
        _role_work(
            view,
            event,
            role="engineer",
            kind="task",
            title=_text(event, "title", 240) or "Mission started",
            detail=_text(event, "objective", 4000),
            status="active",
        )

    elif event_type in {EventType.LIFE_MISSION_COMPLETED, EventType.LIFE_MISSION_FAILED}:
        mission_status, role_status, label, tone = _mission_outcome_presentation(
            event,
            event_type,
        )
        final_output = str(event.get("final_output") or "").strip()
        mission.update({
            "id": _text(event, "item_id") or mission.get("id", ""),
            "title": _text(event, "title", 240) or mission.get("title", ""),
            "objective": _text(event, "objective", 2000) or mission.get("objective", ""),
            "summary": _text(event, "summary", 1200),
            "final_output": final_output or mission.get("final_output", ""),
            "status": mission_status,
            "completed_at": ts,
        })
        raw_outcome = event.get("outcome")
        if isinstance(raw_outcome, dict):
            view["outcome"] = dict(raw_outcome)
        else:
            view["outcome"] = mission_outcome_dimensions(
                status=_text(event, "status"),
                success=bool(event.get("success")),
                stop_kind=event.get("stop_kind"),
                resumable=bool(event.get("resumable")),
            )
        _set_role(view, "engineer", role_status, label, ts)
        detail = (
            _text(event, "summary", 1200)
            or _text(event, "stop_reason", 2000)
            or _text(event, "failure_reason", 2000)
            or _text(event, "title", 500)
            or _text(event, "status", 500)
        )
        _timeline(
            view,
            event,
            role="engineer",
            title=label,
            detail=detail,
            tone=tone,
        )
        _role_work(
            view,
            event,
            role="engineer",
            kind="completion",
            title=label,
            detail=detail,
            status=mission_status,
        )


def reduce_round_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type == EventType.VENUE_RESEARCH_STARTED:
        label = "Researching target venue"
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "active", label, ts)
        _timeline(view, event, role="engineer", title=label, detail=detail)
        _role_work(
            view,
            event,
            role="engineer",
            kind="venue_research",
            title=label,
            detail=detail,
            status="active",
        )

    elif event_type == EventType.VENUE_RESEARCH_COMPLETED:
        label = "Venue profile ready" if event.get("ok") is True else "Venue research finished"
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "done", label, ts)
        _timeline(view, event, role="engineer", title=label, detail=detail)
        _role_work(
            view,
            event,
            role="engineer",
            kind="venue_research",
            title=label,
            detail=detail,
            status="done",
        )

    elif event_type == EventType.IDEA_SEARCH_STARTED:
        label = "Searching candidate ideas"
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "active", label, ts)
        _timeline(view, event, role="engineer", title=label, detail=detail)
        _role_work(
            view,
            event,
            role="engineer",
            kind="idea_search",
            title=label,
            detail=detail,
            status="active",
        )

    elif event_type == EventType.IDEA_SEARCH_COMPLETED:
        label = "Candidate ideas ready"
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "done", label, ts)
        _timeline(view, event, role="engineer", title=label, detail=detail)
        _role_work(
            view,
            event,
            role="engineer",
            kind="idea_search",
            title=label,
            detail=detail,
            status="done",
        )

    elif event_type == EventType.ROUND_START:
        current = _integer(event, "round_index") or 0
        maximum = _integer(event, "round_max") or int(view.get("round", {}).get("max") or 0)
        view["round"] = {"current": current, "max": maximum}
        _set_role(view, "engineer", "active", f"Running round {current}", ts)
        _timeline(view, event, role="engineer", title=f"Round {current} started")

    elif event_type == EventType.ENGINEER_PROGRESS:
        role = _text(event, "agent_layer") or _text(event, "actor") or "engineer"
        if role == "main":
            role = "engineer"
        kind = _text(event, "kind")
        label = _PROGRESS_LABELS.get(kind, "Working")
        _set_role(view, role, "active", label, ts)
        if role == "engineer" and kind in {
            "assistant_message",
            "agent_message",
            "message",
        }:
            candidate = strip_named_lines(
                str(event.get("text") or ""),
                ("MILESTONE_STATUS", "OPERATOR_QUESTION", "OPERATOR_OPTIONS"),
            ).strip()
            if len(candidate) >= len(str(mission.get("final_output") or "")):
                mission["final_output"] = candidate
        detail = (
            _text(event, "action_summary", 4000)
            or _text(event, "text", 4000)
        )
        if detail and _visible_role_work_progress(
            event,
            role=role,
            kind=kind,
            detail=detail,
        ):
            _role_work(
                view,
                event,
                role=role,
                kind=kind or "progress",
                title=label,
                detail=detail,
                status="active",
            )
        if kind not in {"reasoning", "assistant_message", "agent_message"}:
            _timeline(view, event, role=role, title=label, detail=_text(event, "action_summary") or _text(event, "text"))

    elif event_type == EventType.ROUND_REVIEW_STARTED:
        _set_role(view, "reviewer", "active", "Reviewing benchmark evidence", ts)
        _role_work(
            view,
            event,
            role="reviewer",
            kind="review",
            title="Review started",
            status="active",
        )

    elif event_type == EventType.ROUND_MAIN_COMPLETED:
        _set_role(view, "engineer", "done", "Engineer handoff ready", ts)
        _role_work(
            view,
            event,
            role="engineer",
            kind="handoff",
            title="Engineer handoff ready",
            detail=_text(event, "text", 4000)
            or _text(event, "summary", 4000),
            status="done",
        )

    elif event_type == EventType.ROUND_REVIEW_DEFERRED:
        next_step = _text(event, "next_step")
        _set_role(view, "engineer", "active", "Continuing before review", ts)
        _set_role(view, "reviewer", "waiting", "Review deferred for one round", ts)
        _timeline(
            view,
            event,
            role="engineer",
            title="Continued before review",
            detail=next_step,
            tone="info",
        )

    elif event_type == EventType.ROUND_REVIEW_COMPLETED:
        status = _text(event, "status")
        reason = _text(event, "reason")
        review_source = _text(event, "review_source") or "reviewer"
        view["review"] = {
            "status": status,
            "reason": reason,
            "source": review_source,
            "rejected_attempts": int(view.get("review", {}).get("rejected_attempts") or 0)
            + (1 if status in {"continue", "blocked"} else 0),
        }
        frontier_change = _text(event, "frontier_change")
        if frontier_change:
            view["frontier"] = {
                "change": frontier_change,
                "summary": _text(event, "frontier_summary", 2000),
                "updated_at": ts,
            }
        if review_source == "engineer_self_review" and status == "done":
            _set_role(view, "engineer", "done", "Self-verified", ts)
            _set_role(view, "reviewer", "done", "Independent review not required", ts)
            _timeline(
                view,
                event,
                role="engineer",
                title="Engineer self-review accepted",
                detail=reason,
                tone="success",
            )
        else:
            _set_role(view, "reviewer", "done" if status == "done" else "rejected", "Accepted evidence" if status == "done" else "Requested another attempt", ts)
            _timeline(
                view,
                event,
                role="reviewer",
                title="Evidence accepted" if status == "done" else "Attempt rejected",
                detail=reason,
                tone="success" if status == "done" else "error",
            )
        detail = reason
        next_action = _text(event, "next_action", 2000)
        if next_action:
            detail = f"{detail}\n\nNext action: {next_action}".strip()
        _role_work(
            view,
            event,
            role="reviewer",
            kind="verdict",
            title="Evidence accepted" if status == "done" else "Attempt rejected",
            detail=detail,
            status=status,
        )
        if status == "done":
            round_index = _integer(event, "round_index")
            candidates = [
                metric for metric in view.setdefault("metrics", [])
                if metric.get("verification_status") == "reported"
                and (round_index is None or metric.get("round_index") in {None, round_index})
            ]
            if candidates:
                candidates[-1].update({
                    "verification_status": "accepted",
                    "reviewer_reason": reason,
                    "verified_at": ts,
                    "verification_source": "round.review.completed",
                })
