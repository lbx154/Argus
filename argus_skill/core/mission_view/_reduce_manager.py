"""Manager and Planner mission-view event-family reducers.

Manager events project the moment the request is understood and the stage
decisions that follow; Planner events project the continuous-mode scheduling
lifecycle (start / task added / plan settled / waiting / idle / error). Both
are Manager/Planner-authored decisions and this module only projects their
structured fields into the read model, with a sentence a reader outside the
team understands, in the session's language.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..event_catalog import EventType
from ._reduce_helpers import _role_work, _set_role, _text, _timeline, _upsert
from ._wording import (
    remember_language,
    say,
    session_is_chinese,
    stage_label,
    stage_name,
)

_STAGE_DECISION_KINDS = {
    "advance": "stage_advanced",
    "hold": "stage_held",
    "rollback": "stage_rolled_back",
    "complete": "stage_completed",
}


def reduce_manager_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type == EventType.LIFE_MANAGER_INTENT_STARTED:
        item_id = _text(event, "item_id") or _text(event, "intent_id")
        objective = _text(event, "objective", 2000)
        remember_language(view, objective)
        chinese = session_is_chinese(view, objective)
        mission.update({
            "id": item_id,
            "title": objective[:180],
            "objective": objective,
            "summary": "",
            "final_output": "",
            "started_at": None,
            "completed_at": None,
            "status": "grounding",
        })
        label = say("grounding_started", chinese)
        _set_role(view, "manager", "active", label, ts, kind="grounding_started")
        _timeline(
            view,
            event,
            role="manager",
            kind="grounding_started",
            title=label,
            detail=objective[:500],
        )
        _role_work(
            view,
            event,
            role="manager",
            kind="grounding",
            title=label,
            detail=objective,
            status="active",
        )

    elif event_type == EventType.LIFE_MANAGER_INTENT_COMPLETED:
        item_id = _text(event, "item_id") or _text(event, "intent_id")
        objective = _text(event, "objective", 2000) or _text(event, "execution_task", 2000)
        remember_language(view, objective)
        chinese = session_is_chinese(view, objective)
        mission.update({
            "id": item_id,
            "title": objective[:180],
            "objective": objective,
            "summary": "",
            "final_output": "",
            "started_at": None,
            "completed_at": None,
            "status": "framed",
        })
        routing = dict(view.get("routing") or {})
        for key in ("route", "vertical", "workflow_mode", "lifetime"):
            value = _text(event, key)
            if value:
                routing[key] = value
        if not routing.get("route"):
            routing["route"] = "team"
        for key in ("continuous", "open_ended"):
            if key in event:
                routing[key] = event.get(key) is True
        view["routing"] = routing
        current_stage = _text(event, "current_stage")
        stages = event.get("stages")
        if current_stage:
            view["stage"] = {
                "id": current_stage,
                "label": stage_label(current_stage, chinese),
            }
        elif (
            isinstance(stages, list)
            and stages
            and not _text(view.get("stage", {}), "id")
        ):
            stage = str(stages[0] or "").strip()
            view["stage"] = {"id": stage, "label": stage_label(stage, chinese)}
        label = say("goal_framed", chinese)
        _set_role(view, "manager", "done", label, ts, kind="goal_framed")
        _timeline(
            view,
            event,
            role="manager",
            kind="goal_framed",
            title=label,
            detail=_text(event, "reason"),
            tone="success",
        )
        _role_work(
            view,
            event,
            role="manager",
            kind="decision",
            title=label,
            detail=_text(event, "reason", 4000)
            or _text(event, "execution_task", 4000),
            status="done",
        )

    elif event_type == EventType.LIFE_MANAGER_INTENT_FAILED:
        mission["status"] = "failed"
        chinese = session_is_chinese(view, _text(event, "objective", 2000))
        title = say("goal_not_understood", chinese)
        detail = say("goal_not_understood_detail", chinese)
        _set_role(view, "manager", "error", title, ts, kind="goal_not_understood")
        _timeline(
            view,
            event,
            role="manager",
            kind="goal_not_understood",
            title=title,
            detail=detail,
            tone="error",
        )
        _role_work(
            view,
            event,
            role="manager",
            kind="grounding",
            title=title,
            detail=detail,
            status="error",
        )

    elif event_type == EventType.LIFE_MANAGER_STAGE_DECISION:
        stage = _text(event, "target_stage") or _text(event, "stage") or _text(event, "current_stage")
        chinese = session_is_chinese(view, _text(event, "reason"))
        action = _text(event, "action").strip().lower()
        kind = _STAGE_DECISION_KINDS.get(action, "stage_reconsidered")
        title = say(
            kind,
            chinese,
            stage=stage_name(stage, chinese) or ("这个" if chinese else "current"),
        )
        if stage:
            view["stage"] = {"id": stage, "label": stage_label(stage, chinese)}
        _set_role(view, "manager", "done", title, ts, kind=kind)
        _timeline(
            view,
            event,
            role="manager",
            kind=kind,
            title=title,
            detail=_text(event, "reason"),
        )
        _role_work(
            view,
            event,
            role="manager",
            kind="stage_decision",
            title=title,
            detail=_text(event, "reason", 4000),
            status="done",
        )


def reduce_planner_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    chinese = session_is_chinese(
        view,
        _text(event, "objective", 2000),
        _text(event, "title", 240),
    )
    if event_type == EventType.LIFE_PLANNER_START:
        label = say("planning_started", chinese)
        _set_role(view, "planner", "active", label, ts, kind="planning_started")
        _role_work(
            view,
            event,
            role="planner",
            kind="planning",
            title=label,
            detail=_text(event, "objective", 4000),
            status="active",
        )

    elif event_type == EventType.LIFE_PLANNER_TASK_ADDED:
        item_id = _text(event, "item_id")
        raw_deps = event.get("deps")
        deps = list(raw_deps) if isinstance(raw_deps, list) else []
        _upsert(view.setdefault("dag", []), "id", item_id, {
            "id": item_id,
            "title": _text(event, "title", 240),
            "objective": _text(event, "objective", 1000),
            "status": "pending",
            "deps": [str(dep) for dep in deps if str(dep).strip()],
            "branch_id": _text(event, "branch_id") or item_id,
            "parent_branch_id": _text(event, "parent_branch_id") or None,
        })
        kind = (
            "research_route_added"
            if _text(view.get("routing", {}), "vertical") == "research"
            else "task_added"
        )
        label = say(kind, chinese)
        _set_role(view, "planner", "done", label, ts, kind=kind)
        _timeline(
            view,
            event,
            role="planner",
            kind=kind,
            title=label,
            detail=_text(event, "title"),
            tone="info",
        )
        _role_work(
            view,
            event,
            role="planner",
            kind="task",
            title=_text(event, "title", 240) or label,
            detail=_text(event, "objective", 4000),
            status="pending",
        )

    elif event_type == EventType.LIFE_PLANNER_VERDICT:
        project_done = bool(event.get("project_done"))
        raw_delivery = event.get("delivery")
        delivery = (
            dict(raw_delivery)
            if project_done and isinstance(raw_delivery, dict)
            else None
        )
        kind = (
            "task_delivered"
            if delivery is not None
            else "project_finished"
            if project_done
            else "planning_complete"
        )
        label = say(kind, chinese)
        if delivery is not None:
            view["delivery"] = delivery
            mission = view.setdefault("mission", {})
            mission["status"] = "complete"
            mission["summary"] = str(delivery.get("summary") or "")[:1200]
            mission["completed_at"] = ts
        _set_role(view, "planner", "done", label, ts, kind=kind)
        _timeline(
            view,
            event,
            role="planner",
            kind=kind,
            title=label,
            detail=_text(event, "reason"),
            tone="success" if project_done else "neutral",
        )
        _role_work(
            view,
            event,
            role="planner",
            kind="verdict",
            title=label,
            detail=_text(event, "reason", 4000),
            status="done" if project_done else "planned",
        )

    elif event_type == EventType.LIFE_PLANNER_WAITING:
        label = say("planner_waiting", chinese)
        _set_role(view, "planner", "waiting", label, ts, kind="planner_waiting")
        _timeline(
            view,
            event,
            role="planner",
            kind="planner_waiting",
            title=label,
            detail=_text(event, "reason") or _text(event, "waiting_reason"),
        )
        _role_work(
            view,
            event,
            role="planner",
            kind="waiting",
            title=label,
            detail=_text(event, "reason", 4000)
            or _text(event, "waiting_reason", 4000),
            status="waiting",
        )

    elif event_type == EventType.LIFE_PLANNER_TERMINAL_IDLE:
        label = say("planner_idle", chinese)
        _set_role(view, "planner", "waiting", label, ts, kind="planner_idle")
        _timeline(
            view,
            event,
            role="planner",
            kind="planner_idle",
            title=label,
            detail=_text(event, "reason"),
        )

    elif event_type == EventType.LIFE_PLANNER_ERROR:
        label = say("planning_failed", chinese)
        _set_role(view, "planner", "error", label, ts, kind="planning_failed")
        _timeline(
            view,
            event,
            role="planner",
            kind="planning_failed",
            title=label,
            detail=_text(event, "error") or _text(event, "reason"),
            tone="error",
        )
