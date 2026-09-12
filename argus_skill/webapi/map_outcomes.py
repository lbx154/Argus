"""Read-only attempt attribution for map outcomes, independent of scheduling.

Like mission_view, a newer mission start establishes a fresh attempt; an older
settlement is history. The backlog intentionally keeps its last outcome across
resume, so its mere presence never establishes a result for the new attempt.
"""
from __future__ import annotations

import math

from ..core.secret_guard import redact_secrets_text
from ..life.mission_outcome import mission_outcome_dimensions

_START = "life.mission.started"
_TERMINAL = frozenset({"life.mission.completed", "life.mission.failed"})
_UNSETTLED = frozenset({"pending", "queued", "running", "in_progress", "claimed"})


def public_outcome(value) -> dict:
    """Keep recorded dimensions without promoting an executor report to review."""
    if not isinstance(value, dict):
        return {}
    result = {
        key: redact_secrets_text(value[key])[:120]
        for key in ("execution_status", "review_status", "stage_certification", "interruption_kind")
        if isinstance(value.get(key), str)
    }
    if isinstance(value.get("resumable"), bool):
        result["resumable"] = value["resumable"]
    return result


def _timestamp(value):
    return float(value) if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def _attempt(value):
    return value if type(value) is int and value >= 1 else None


def _event_outcome(event):
    if isinstance(event.get("outcome"), dict):
        return public_outcome(event["outcome"])
    # Reuse the canonical execution classification for legacy lifecycle records,
    # but do not invent absent review/certification/resumption dimensions.
    if not event.get("status") and type(event.get("success")) is not bool:
        return {}
    dimensions = mission_outcome_dimensions(status=event.get("status", ""), success=event.get("success") is True)
    return public_outcome({
        "execution_status": dimensions["execution_status"],
        **{key: event[key] for key in ("stage_certification", "resumable") if key in event},
        **({"interruption_kind": event["stop_kind"]} if event.get("stop_kind") else {}),
    })


def _preceding_start(event, starts):
    return next((start for start in reversed(starts) if start["ts"] <= event["ts"]), None)


def _source(event, starts, *, floor=None):
    start = _preceding_start(event, starts)
    if start is not None and floor is not None and start["ts"] < floor:
        start = None
    declared = _attempt(event.get("attempt"))
    return {
        "event_id": event["id"], "event_type": event["type"], "event_ts": event["ts"],
        "event_attempt": declared,
        "attempt": declared if declared is not None else _attempt((start or {}).get("attempt")),
        "start_event_id": (start or {}).get("id"), "start_event_ts": (start or {}).get("ts"),
    }


def project_task_outcome(task: dict, events: list[dict]) -> dict:
    """Return a response copy with separate current and retained outcome facts.

    Only a same-task terminal event after an explicit attempt/claim boundary can
    establish the current outcome. Missing starts, conflicting attempts, and
    still-pending/running tasks keep their old backlog outcome as retained data.
    """
    owned = sorted((event for event in events
                    if event.get("item_id") == task.get("id") and event.get("id")
                    and _timestamp(event.get("ts")) is not None), key=lambda event: (event["ts"], event["id"]))
    starts = [event for event in owned if event.get("type") == _START]
    terminals = [event for event in owned if event.get("type") in _TERMINAL and _event_outcome(event)]
    if "outcome" not in task and "recorded_outcome" not in task and not terminals:
        return dict(task)
    recorded = public_outcome(task.get("recorded_outcome", task.get("outcome")))
    attempt = _attempt(task.get("attempt"))
    started = _timestamp(task.get("started_ts"))
    compatible = [event for event in starts
                  if (started is None or event["ts"] >= started)
                  and (attempt is None or _attempt(event.get("attempt")) in {None, attempt})]
    # A pending resumed task with an incremented attempt has no current start;
    # an old start without an attempt number cannot fill that gap by adjacency.
    if started is None and attempt is not None:
        compatible = [event for event in compatible if _attempt(event.get("attempt")) == attempt]
    start = compatible[-1] if compatible else None
    floor = max(value for value in (started, (start or {}).get("ts")) if value is not None) if started is not None or start else None
    active = task.get("status") in _UNSETTLED
    selected = None
    if floor is not None and not active:
        for event in terminals:
            source = _source(event, starts, floor=floor)
            if event["ts"] < floor or (attempt is not None and source["attempt"] not in {None, attempt}):
                continue
            # An explicit later start for another attempt rules out reusing a
            # result merely because the retained task row has not caught up.
            later_starts = [item for item in starts if item["ts"] > floor and item["ts"] <= event["ts"]
                            and attempt is not None and _attempt(item.get("attempt")) not in {None, attempt}]
            if not later_starts:
                selected = event
    current_source = {
        "status": "current_attempt" if selected else "not_recorded_for_current_attempt",
        "attempt": attempt, "started_ts": started,
        "start_event_id": (start or {}).get("id"), "start_event_ts": (start or {}).get("ts"),
    }
    if selected:
        current_source.update(_source(selected, starts, floor=floor), started_ts=started)
        if attempt is not None:
            current_source["attempt"] = attempt
        current_source["binding"] = "attempt_and_start" if attempt is not None else "same_task_start_window"
    else:
        current_source["reason"] = "task_unsettled" if active else "start_boundary_unavailable" if floor is None else "no_matching_terminal_event"
    recorded_source = {"status": "unbound" if recorded else "not_recorded"}
    if recorded:
        matches = [event for event in terminals if _event_outcome(event) == recorded]
        if matches:
            event = matches[-1]
            source = _source(event, starts, floor=floor if event is selected else None)
            historical = ((floor is not None and event["ts"] < floor)
                          or (attempt is not None and source["attempt"] is not None and source["attempt"] != attempt))
            recorded_source = {**source, "status": "current_attempt" if event is selected
                               else "historical_attempt" if historical else "unbound"}
    return {**task, "outcome": _event_outcome(selected) if selected else {},
            "outcome_source": current_source, "recorded_outcome": recorded,
            "recorded_outcome_source": recorded_source}


def project_map_outcomes(tasks: list[dict], events: list[dict]) -> list[dict]:
    by_task: dict[str, list[dict]] = {}
    for event in events:
        by_task.setdefault(event.get("item_id", ""), []).append(event)
    return [project_task_outcome(task, by_task.get(task["id"], [])) for task in tasks]
