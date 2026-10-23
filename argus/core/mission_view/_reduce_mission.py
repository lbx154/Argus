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
from ..stop_kinds import (
    NON_FAILURE_STOP_KINDS,
    is_pause_status,
    normalize_stop_kind,
    park_clause,
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
# Failures the runtime names on its events when the call never had a working
# model service behind it (``failure_kind``), each with the line of the
# runner's record that says what was wrong (``failure_cause``). The codes are
# the round loop's own; the sentences for them live in ``_wording``.
_NAMED_FAILURE_KINDS = frozenset({
    "service_unreachable",
    "service_error",
    "service_tls",
    "service_proxy",
    "cli_missing",
    "service_quota",
    "model_catalog",
    "sign_in",
})


def _named_failure(event: Mapping[str, Any]) -> tuple[str, str]:
    """The infrastructure failure an event names, as ``(kind, cause line)``."""
    kind = _text(event, "failure_kind")
    line = _text(event, "failure_cause", 200)
    if kind in _NAMED_FAILURE_KINDS and line:
        return kind, line
    return "", ""


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

    Returns ``(cause code, stop kind)``. Who stopped the round is its stop
    kind; otherwise the fixed ``review: skipped (…)`` opening the runtime
    writes into the event text names the path that skipped.
    """
    stop_kind = normalize_stop_kind(event.get("stop_kind")) or ""
    named_kind, _named_line = _named_failure(event)
    if named_kind:
        return named_kind, stop_kind
    if stop_kind == "daemon_shutdown":
        return "argus_stopped", stop_kind
    if stop_kind == "operator_abort":
        return "task_cancelled", stop_kind
    match = _SKIPPED_OPENING_RE.match(_text(event, "text", 400))
    opening = match.group(1).strip().casefold() if match else ""
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


def _settled_status(event: Mapping[str, Any]) -> str:
    """How a review or a task settled, as its reader is told; ``settledStatus``
    in frontend/core/src/missionOutcome.ts says the same. A ``→ operator:``
    handoff settles ``blocked`` with the question it asks (HANDOFF_STATUS):
    the work waits on the operator's decision and is not stuck."""
    status = _text(event, "status").lower()
    return "question" if status == "blocked" and _text(event, "operator_question") else status


def _mission_outcome_presentation(
    event: Mapping[str, Any],
    event_type: str,
    *,
    chinese: bool,
) -> tuple[str, str, str, str, str, str]:
    """Return (mission status, role status, kind, label, tone, technical)."""
    if _settled_status(event) == "question":
        # A task that settled on a question to the operator is parked on it, as its row is.
        event = {**event, "outcome_class": "ended", "status": "parked", "park_reason": "operator_question"}
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
                park_reason=event.get("park_reason"),
            )
        )
    mission_status, role_status, kind, tone = _MISSION_OUTCOME_PRESENTATIONS[
        outcome_class
    ]
    technical = ""
    if _text(event, "status") == "stage_continues" or (
        outcome_class == "completed" and event.get("campaign_continues") is True
    ):
        mission_status, kind, tone = "continued", "mission_continued", "info"
    elif (
        outcome_class == "completed"
        and event.get("final_submission_certified") is True
    ):
        kind = "mission_certified"
    elif outcome_class == "ended":
        raw_status = _text(event, "status")
        technical = raw_status
        if is_pause_status(raw_status) or event.get("resumable") is True:
            outcome = event.get("outcome") if isinstance(event.get("outcome"), dict) else {}
            stop_kind = event.get("stop_kind") or outcome.get("interruption_kind")
            named_kind, named_line = _named_failure(event)
            why = (
                say(f"why_{named_kind}", chinese, cause=named_line)
                if named_kind
                else park_clause(raw_status, event.get("park_reason"), chinese=chinese)
                or stop_kind_clause(stop_kind, chinese=chinese, neutral=True)
            )
            kind = "mission_paused"
            return mission_status, role_status, kind, say(kind, chinese, why=why), tone, technical
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
                park_reason=event.get("park_reason"),
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
            # Without the Engineer's own summary the closing row says why the
            # task stopped: the failure or stop the event names, with the
            # runtime's own record kept apart; otherwise the reason as written.
            reason = _text(event, "stop_reason", 2000) or _text(event, "failure_reason", 2000)
            named_kind, named_line = _named_failure(event)
            why = stop_kind_clause(event.get("stop_kind"), chinese=chinese)
            if named_kind or why:
                detail = (
                    say(f"cause_{named_kind}", chinese, cause=named_line)
                    if named_kind
                    else say("task_stopped", chinese, why=why)
                )
                cause = named_kind
                technical = "; ".join(part for part in (technical, _technical_note(reason)) if part)
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
    if review_skipped:
        cause, stop_kind = _skipped_round_cause(event)
        _named_kind, named_line = _named_failure(event)
        title = say("round_not_judged", chinese)
        explanation = (
            say("cause_paused", chinese, why=stop_kind_clause(stop_kind, chinese=chinese, neutral=True))
            if cause == "paused"
            else say(f"cause_{cause}", chinese, cause=named_line)
            if named_line
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
        settled = _settled_status(event)
        kind = {
            "done": "results_accepted",
            "continue": "another_attempt_requested",
            "blocked": "attempt_blocked",
            "question": "choice_left_to_you",
            "replan_requested": "replan_requested",
        }.get(settled, "results_not_final")
        role_status, tone = (
            ("done", "success") if status == "done"
            else ("waiting", "info") if settled == "question"
            else ("rejected", "error")
        )
        _set_role(view, "reviewer", role_status, say(kind, chinese), ts, kind=kind)
        _timeline(
            view,
            event,
            role="reviewer",
            kind=kind,
            title=say(kind, chinese),
            detail=reason,
            tone=tone,
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
        # A Manager turn step carries its own status and is written twice
        # (opened, then closed); a closed step is not the Manager being active.
        turn_step = event.get("turn_step") is True
        step_status = _text(event, "status", 40) if turn_step else ""
        if not (turn_step and step_status and step_status != "running"):
            _set_role(view, role, "active", label, ts, kind=progress_kind)
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
                status=step_status or "active",
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
