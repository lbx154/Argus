"""Mission-lifecycle and round/engineer mission-view event-family reducers.

Covers the start/complete/fail of a mission plus the per-round sequence in
which the Engineer works and the Reviewer judges. This module only projects
structured event fields into the read model; it never re-derives Reviewer
judgments or Manager stage authority — those decisions already happened
upstream and are carried verbatim on the event payload.

What a reader sees is a sentence chosen by code (see ``_wording``), in the
session's language. A round that produced no judgment is explained from the
event's structured fields; the runtime's own record of why (exit codes, retry
counts) is kept beside the sentence in a ``technical`` field, never inside it.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from ...life.mission_outcome import mission_outcome_class, mission_outcome_dimensions
from ..event_catalog import EventType
from ..role_reply import strip_named_lines
from ..stop_kinds import (
    NON_FAILURE_STOP_KINDS,
    normalize_stop_kind,
    pause_status_clause,
    stop_kind_clause,
)
from ._reduce_helpers import (
    _integer,
    _progress_kind,
    _role_work,
    _set_role,
    _text,
    _timeline,
    _visible_role_work_progress,
)
from ._wording import remember_language, say, session_is_chinese

# outcome class -> (mission status, role status, sentence code, tone)
_MISSION_OUTCOME_PRESENTATIONS = {
    "completed": ("complete", "done", "mission_completed", "success"),
    "incomplete": ("incomplete", "done", "mission_incomplete", "info"),
    "stalled": ("stalled", "done", "mission_stalled", "info"),
    "blocked": ("blocked", "error", "mission_blocked", "error"),
    "failed": ("failed", "error", "mission_failed", "error"),
    "ended": ("ended", "done", "mission_ended", "info"),
}

# The runtime marks the technical part of its own sentences with one of
# these; older records carried bare ``key=value`` fragments instead.
_TECHNICAL_MARKER_RE = re.compile(
    r"(?:Runner receipt|Technical record|技术记录)\s*[:：]\s*",
    re.IGNORECASE,
)
_TECHNICAL_FRAGMENT_RE = re.compile(
    r"\b(?:backend_failure_streak|fatal_error|error|exit|stop_kind)=[^;)\n]+"
)
_SKIPPED_OPENING_RE = re.compile(r"^review:\s*skipped\s*\(([^)]*)\)", re.IGNORECASE)
_PAUSE_STOP_KINDS = frozenset(NON_FAILURE_STOP_KINDS) - {"daemon_shutdown", "operator_abort"}


def _technical_note(reason: str) -> str:
    """The runtime's own record of a failure, separated from the sentence."""
    text = str(reason or "").strip()
    marker = _TECHNICAL_MARKER_RE.search(text)
    if marker:
        return text[marker.end():].strip()
    fragments = [fragment.strip() for fragment in _TECHNICAL_FRAGMENT_RE.findall(text)]
    if fragments:
        return "; ".join(fragments)
    return text


def _skipped_round_cause(event: Mapping[str, Any]) -> tuple[str, str]:
    """Classify why a round was not judged from the event's structured fields.

    Returns ``(cause code, stop kind)``. The stop kind comes from the event
    when it carries one; otherwise the fixed ``review: skipped (…)`` opening
    the runtime writes into the event text names the path that skipped.
    """
    stop_kind = normalize_stop_kind(event.get("stop_kind")) or ""
    opening = ""
    match = _SKIPPED_OPENING_RE.match(_text(event, "text", 400))
    if match:
        opening = match.group(1).strip().casefold()
        stop_kind = stop_kind or (normalize_stop_kind(opening) or "")
    if stop_kind == "daemon_shutdown" or "daemon stop" in opening:
        return "argus_stopped", stop_kind
    if stop_kind == "operator_abort" or "operator abort" in opening:
        return "task_cancelled", stop_kind
    if "reviewer backend" in opening:
        return "reviewer_unreachable", stop_kind
    if "provider-turn" in opening or "provider turn" in opening:
        return "session_length_limit", stop_kind
    if "execution host" in opening:
        return "tools_unavailable", stop_kind
    if "model unavailable" in opening:
        return "model_unavailable", stop_kind
    if "auth" in opening or "sign in" in opening or "login" in opening:
        return "sign_in_failed", stop_kind
    if stop_kind in _PAUSE_STOP_KINDS:
        return "paused", stop_kind
    if "backend failure" in opening:
        return "engineer_service_dropped", stop_kind
    if event.get("backend_unavailable") is True or stop_kind in {
        "backend_unavailable",
        "transient_error",
    }:
        return (
            "reviewer_unreachable"
            if _text(event, "review_source") == "reviewer" and not opening
            else "engineer_service_dropped",
            stop_kind,
        )
    return "unknown", stop_kind


# Openings the runtime itself writes when it stops a task (both the wording
# in use and the wording of records written before it changed), mapped to the
# code of the sentence that explains them. A Reviewer's or Engineer's own prose
# never starts this way, so it is left untouched.
_RUNTIME_STOP_OPENINGS: tuple[tuple[str, str], ...] = (
    ("reviewer backend unavailable for", "reviewer_unreachable"),
    ("the reviewer could not reach a judgment", "reviewer_unreachable"),
    ("审阅者连续", "reviewer_unreachable"),
    ("engineer backend failed before a trustworthy", "engineer_service_dropped"),
    ("the model service dropped the engineer's session", "engineer_service_dropped"),
    ("the wait after a repeated backend failure ended early", "retry_wait_cut_short"),
    ("argus was stopped by its operator", "argus_stopped"),
    ("execution host is unavailable", "tools_unavailable"),
    ("the tool environment the engineer needs", "tools_unavailable"),
    ("configured model is unavailable", "model_unavailable"),
    ("the configured model is unavailable", "model_unavailable"),
    ("one engineer call used its whole per-call provider-turn", "session_length_limit"),
    ("the engineer's session reached the length limit", "session_length_limit"),
    ("backend call paused before a trustworthy", "paused"),
    ("the work was paused before the engineer finished", "paused"),
)
_ATTEMPT_COUNT_RE = re.compile(r"(\d+)\s*(?:consecutive|times?\b)|连续\s*(\d+)\s*次")


def _runtime_stop_record(
    reason: str,
    *,
    chinese: bool,
    stop_kind: str | None,
) -> tuple[str, str, str] | None:
    """Explain a task-closing reason the runtime wrote, or return None.

    Returns ``(sentence, cause, technical)``. Only records that begin with one
    of the runtime's own openings are rewritten; anything else is a role's own
    words and is shown as it is.
    """
    text = str(reason or "").strip()
    lowered = text.casefold()
    cause = next(
        (code for opening, code in _RUNTIME_STOP_OPENINGS if lowered.startswith(opening)),
        "",
    )
    if not cause:
        return None
    technical = _technical_note(text)
    if technical == text:
        technical = ""
    if cause == "reviewer_unreachable":
        match = _ATTEMPT_COUNT_RE.search(text)
        attempts = next((group for group in (match.groups() if match else ()) if group), "")
        sentence = (
            say("task_stopped_reviewer_unreachable", chinese, attempts=attempts)
            if attempts
            else say("task_stopped_reviewer_unreachable_repeatedly", chinese)
        )
    elif cause in {"engineer_service_dropped", "argus_stopped", "retry_wait_cut_short"}:
        sentence = say(f"task_stopped_{cause}", chinese)
    elif cause == "paused":
        sentence = say(
            "cause_paused",
            chinese,
            why=stop_kind_clause(stop_kind, chinese=chinese)
            or ("工作被打断" if chinese else "the work was interrupted"),
        )
    else:
        sentence = say(f"cause_{cause}", chinese)
    return sentence, cause, technical


def _mission_outcome_presentation(
    event: Mapping[str, Any],
    event_type: str,
    *,
    chinese: bool,
) -> tuple[str, str, str, str, str, str]:
    """Return (mission status, role status, kind, label, tone, technical)."""
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
    mission_status, role_status, kind, tone = _MISSION_OUTCOME_PRESENTATIONS[
        outcome_class
    ]
    technical = ""
    if outcome_class == "completed" and event.get("campaign_continues") is True:
        mission_status, kind, tone = "continued", "mission_continued", "info"
    elif (
        outcome_class == "completed"
        and event.get("final_submission_certified") is True
    ):
        kind = "mission_certified"
    elif outcome_class == "ended":
        raw_status = _text(event, "status")
        technical = raw_status
        outcome = event.get("outcome") if isinstance(event.get("outcome"), dict) else {}
        stop_kind = (
            normalize_stop_kind(event.get("stop_kind"))
            or normalize_stop_kind(outcome.get("interruption_kind"))
        )
        why = pause_status_clause(raw_status, chinese=chinese) or stop_kind_clause(
            stop_kind, chinese=chinese
        )
        if raw_status.lower().startswith("paused_") or event.get("resumable") is True:
            kind = "mission_paused"
            return (
                mission_status,
                role_status,
                kind,
                say(
                    kind,
                    chinese,
                    why=why or ("工作被打断" if chinese else "the work was interrupted"),
                ),
                tone,
                technical,
            )
    return mission_status, role_status, kind, say(kind, chinese), tone, technical


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
        objective = _text(event, "objective", 2000)
        if not view.get("language"):
            remember_language(view, objective or _text(event, "title", 240))
        chinese = session_is_chinese(view, objective, _text(event, "title", 240))
        mission.update({
            "id": _text(event, "item_id"),
            "title": _text(event, "title", 240),
            "objective": objective,
            "summary": "",
            "final_output": "",
            "status": "working",
            "started_at": ts,
            "completed_at": None,
        })
        # Review state is mission-scoped.  Without an explicit reset, a newly
        # started mission inherits the previous mission's accepted/rejected
        # judgment in mission-view.json until its first review finishes.  The
        # execution loop does not use that stale projection for adjudication,
        # but operators and supervision tooling must not mistake it for current
        # evidence.
        view["review"] = {"status": "", "reason": "", "rejected_attempts": 0}
        view["delivery"] = None
        view["outcome"] = {}
        _set_role(
            view,
            "reviewer",
            "waiting",
            say("awaiting_engineer", chinese),
            ts,
            kind="awaiting_engineer",
        )
        label = say("mission_started", chinese)
        _set_role(view, "engineer", "active", label, ts, kind="mission_started")
        _timeline(
            view,
            event,
            role="engineer",
            kind="mission_started",
            title=label,
            detail=_text(event, "title"),
            tone="info",
        )
        _role_work(
            view,
            event,
            role="engineer",
            kind="task",
            title=_text(event, "title", 240) or label,
            detail=_text(event, "objective", 4000),
            status="active",
        )

    elif event_type in {EventType.LIFE_MISSION_COMPLETED, EventType.LIFE_MISSION_FAILED}:
        chinese = session_is_chinese(
            view,
            _text(event, "objective", 2000),
            _text(event, "title", 240),
        )
        mission_status, role_status, kind, label, tone, technical = (
            _mission_outcome_presentation(event, event_type, chinese=chinese)
        )
        final_output = (
            str(event.get("final_output") or "").strip()
            if "final_output" in event
            else mission.get("final_output", "")
            if _text(event, "item_id") == mission.get("id")
            and mission.get("started_at") is not None
            and mission.get("completed_at") in {None, ts}
            else ""
        )
        mission.update({
            "id": _text(event, "item_id") or mission.get("id", ""),
            "title": _text(event, "title", 240) or mission.get("title", ""),
            "objective": _text(event, "objective", 2000) or mission.get("objective", ""),
            "summary": _text(event, "summary", 1200),
            "final_output": final_output,
            "status": mission_status,
            "completed_at": ts,
        })
        raw_delivery = event.get("delivery")
        if bool(event.get("success")) and isinstance(raw_delivery, dict):
            view["delivery"] = dict(raw_delivery)
        elif not bool(event.get("success")):
            view["delivery"] = None
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
        if event.get("final_submission_certified") is True:
            view["outcome"]["final_submission_certified"] = True
            for key in ("venue_review", "venue_review_snapshot"):
                if isinstance(event.get(key), dict):
                    view["outcome"][key] = dict(event[key])
            if isinstance(event.get("manuscript_snapshot"), dict):
                view["outcome"]["manuscript_snapshot"] = dict(
                    event["manuscript_snapshot"]
                )
        _set_role(view, "engineer", role_status, label, ts, kind=kind)
        cause = ""
        detail = _text(event, "summary", 1200)
        if not detail:
            # Without the Engineer's own summary the closing row falls back
            # to the reason the task stopped. When the runtime wrote that
            # reason, say what it means and keep its record apart.
            reason = _text(event, "stop_reason", 2000) or _text(event, "failure_reason", 2000)
            record = _runtime_stop_record(
                reason,
                chinese=chinese,
                stop_kind=normalize_stop_kind(event.get("stop_kind")),
            )
            if record is not None:
                detail, cause, record_technical = record
                technical = "; ".join(part for part in (technical, record_technical) if part)
            else:
                detail = reason or _text(event, "title", 500)
        _timeline(
            view,
            event,
            role="engineer",
            kind=kind,
            title=label,
            detail=detail,
            tone=tone,
            cause=cause,
            technical=technical,
        )
        _role_work(
            view,
            event,
            role="engineer",
            kind="completion",
            title=label,
            detail=detail,
            status=mission_status,
            cause=cause,
            technical=technical,
        )


def _reduce_review_completed(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    ts: float,
    chinese: bool,
) -> None:
    review_skipped = event.get("review_skipped") is True
    status = "skipped" if review_skipped else _text(event, "status")
    reason = _text(event, "reason")
    review_source = _text(event, "review_source") or "reviewer"
    view["review"] = {
        "status": status,
        "reason": reason,
        "source": review_source,
        "rejected_attempts": int(view.get("review", {}).get("rejected_attempts") or 0)
        + (1 if status in {"continue", "blocked"} else 0),
    }
    if isinstance(event.get("manuscript_snapshot"), dict):
        view["review"]["manuscript_snapshot"] = dict(
            event["manuscript_snapshot"]
        )
    frontier_change = _text(event, "frontier_change")
    if frontier_change:
        view["frontier"] = {
            "change": frontier_change,
            "summary": _text(event, "frontier_summary", 2000),
            "updated_at": ts,
        }
    if review_skipped:
        cause, stop_kind = _skipped_round_cause(event)
        title = say("round_not_judged", chinese)
        explanation = (
            say(
                "cause_paused",
                chinese,
                why=stop_kind_clause(stop_kind, chinese=chinese)
                or ("工作被打断" if chinese else "the work was interrupted"),
            )
            if cause == "paused"
            else say(f"cause_{cause}", chinese)
        )
        technical = _technical_note(_text(event, "reason", 2000))
        _set_role(view, "reviewer", "waiting", title, ts, kind="round_not_judged")
        _timeline(
            view,
            event,
            role="reviewer",
            kind="round_not_judged",
            title=title,
            detail=explanation,
            tone="info",
            cause=cause,
            technical=technical,
        )
        _role_work(
            view,
            event,
            role="reviewer",
            kind="review",
            title=title,
            detail=explanation,
            status=status,
            cause=cause,
            technical=technical,
        )
        return

    if review_source == "engineer_self_review" and status == "done":
        kind = "self_check_accepted"
        _set_role(
            view,
            "engineer",
            "done",
            say("self_check_held_up", chinese),
            ts,
            kind="self_check_held_up",
        )
        _set_role(
            view,
            "reviewer",
            "done",
            say("independent_check_not_needed", chinese),
            ts,
            kind="independent_check_not_needed",
        )
        _timeline(
            view,
            event,
            role="engineer",
            kind=kind,
            title=say(kind, chinese),
            detail=reason,
            tone="success",
        )
    else:
        kind = {
            "done": "results_accepted",
            "continue": "another_attempt_requested",
            "blocked": "attempt_blocked",
            "replan_requested": "replan_requested",
        }.get(status, "results_not_final")
        _set_role(
            view,
            "reviewer",
            "done" if status == "done" else "rejected",
            say(kind, chinese),
            ts,
            kind=kind,
        )
        _timeline(
            view,
            event,
            role="reviewer",
            kind=kind,
            title=say(kind, chinese),
            detail=reason,
            tone="success" if status == "done" else "error",
        )
    detail = reason
    next_action = _text(event, "next_action", 2000)
    if next_action:
        detail = f"{detail}\n\n{say('next_action_prefix', chinese)}{next_action}".strip()
    _role_work(
        view,
        event,
        role="reviewer",
        kind="verdict",
        title=say(kind, chinese),
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


def reduce_round_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    chinese = session_is_chinese(view)
    if event_type == EventType.VENUE_RESEARCH_STARTED:
        label = say("venue_research_started", chinese)
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "active", label, ts, kind="venue_research_started")
        _timeline(
            view,
            event,
            role="engineer",
            kind="venue_research_started",
            title=label,
            detail=detail,
        )
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
        kind = "venue_profile_ready" if event.get("ok") is True else "venue_research_finished"
        label = say(kind, chinese)
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "done", label, ts, kind=kind)
        _timeline(view, event, role="engineer", kind=kind, title=label, detail=detail)
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
        label = say("idea_search_started", chinese)
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "active", label, ts, kind="idea_search_started")
        _timeline(
            view,
            event,
            role="engineer",
            kind="idea_search_started",
            title=label,
            detail=detail,
        )
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
        label = say("idea_candidates_ready", chinese)
        detail = _text(event, "text", 4000)
        _set_role(view, "engineer", "done", label, ts, kind="idea_candidates_ready")
        _timeline(
            view,
            event,
            role="engineer",
            kind="idea_candidates_ready",
            title=label,
            detail=detail,
        )
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
        _set_role(
            view,
            "engineer",
            "active",
            say("round_in_progress", chinese, round=current),
            ts,
            kind="round_in_progress",
        )
        _timeline(
            view,
            event,
            role="engineer",
            kind="round_started",
            title=say("round_started", chinese, round=current),
        )

    elif event_type == EventType.ENGINEER_PROGRESS:
        role = _text(event, "agent_layer") or _text(event, "actor") or "engineer"
        if role == "main":
            role = "engineer"
        kind = _text(event, "kind")
        progress_kind = _progress_kind(kind)
        label = say(progress_kind, chinese)
        _set_role(view, role, "active", label, ts, kind=progress_kind)
        if (
            role == "engineer"
            and kind in {"assistant_message", "agent_message", "message"}
            and event.get("final_delivery") is True
            and mission.get("started_at") is not None
            and mission.get("completed_at") is None
            and ts >= mission["started_at"]
            and (not event.get("item_id") or event["item_id"] == mission.get("id"))
        ):
            candidate = strip_named_lines(
                str(event.get("text") or ""),
                (
                    "MILESTONE_STATUS",
                    "NEXT_OWNER",
                    "OPERATOR_QUESTION",
                    "OPERATOR_OPTIONS",
                    "ROLE_DECISION",
                ),
            ).strip()
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
            _timeline(
                view,
                event,
                role=role,
                kind=progress_kind,
                title=label,
                detail=_text(event, "action_summary") or _text(event, "text"),
            )

    elif event_type == EventType.ROUND_REVIEW_STARTED:
        # A fresh check has no judgment yet; keep prior attempts in history,
        # rather than presenting their reason as the current round's result.
        view["review"] = {
            "status": "",
            "reason": "",
            "rejected_attempts": int(view.get("review", {}).get("rejected_attempts") or 0),
        }
        label = say("checking_started", chinese)
        _set_role(view, "reviewer", "active", label, ts, kind="checking_started")
        _role_work(
            view,
            event,
            role="reviewer",
            kind="review",
            title=label,
            status="active",
        )

    elif event_type == EventType.ROUND_MAIN_COMPLETED:
        label = say("engineer_round_finished", chinese)
        _set_role(view, "engineer", "done", label, ts, kind="engineer_round_finished")
        _role_work(
            view,
            event,
            role="engineer",
            kind="handoff",
            title=label,
            detail=_text(event, "text", 4000)
            or _text(event, "summary", 4000),
            status="done",
        )

    elif event_type == EventType.ROUND_REVIEW_DEFERRED:
        next_step = _text(event, "next_step")
        label = say("continuing_before_check", chinese)
        _set_role(view, "engineer", "active", label, ts, kind="continuing_before_check")
        _set_role(
            view,
            "reviewer",
            "waiting",
            say("check_postponed", chinese),
            ts,
            kind="check_postponed",
        )
        _timeline(
            view,
            event,
            role="engineer",
            kind="continuing_before_check",
            title=label,
            detail=next_step,
            tone="info",
        )

    elif event_type == EventType.ROUND_REVIEW_COMPLETED:
        _reduce_review_completed(view, event, ts=ts, chinese=chinese)
