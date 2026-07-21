"""Manager and Planner mission-view event-family reducers.

Manager events project the front-door "goal framed" moment and stage
transition decisions; Planner events project the L4 continuous-mode
scheduling lifecycle (start / task added / verdict / waiting / idle / error).
Both are Manager/Planner-authored decisions and this module only projects
their structured fields into the read model — it makes no stage or quality
judgement of its own.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..event_catalog import EventType
from ._reduce_helpers import _role_work, _set_role, _text, _timeline, _upsert


def reduce_manager_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type == EventType.LIFE_MANAGER_INTENT_COMPLETED:
        view["decision_context"] = {}
        item_id = _text(event, "item_id") or _text(event, "intent_id")
        objective = _text(event, "objective", 2000) or _text(event, "execution_task", 2000)
        mission.update({
            "id": item_id,
            "title": objective[:180],
            "objective": objective,
            "status": "framed",
        })
        current_stage = _text(event, "current_stage")
        stages = event.get("stages")
        if current_stage:
            view["stage"] = {
                "id": current_stage,
                "label": current_stage.replace("_", " ").title(),
            }
        elif (
            isinstance(stages, list)
            and stages
            and not _text(view.get("stage", {}), "id")
        ):
            stage = str(stages[0] or "").strip()
            view["stage"] = {"id": stage, "label": stage.replace("_", " ").title()}
        _set_role(view, "manager", "done", "Goal framed", ts)
        _timeline(view, event, role="manager", title="Goal framed", detail=_text(event, "reason"), tone="success")
        _role_work(
            view,
            event,
            role="manager",
            kind="decision",
            title="Goal framed",
            detail=_text(event, "reason", 4000)
            or _text(event, "execution_task", 4000),
            status="done",
        )

    elif event_type == EventType.LIFE_MANAGER_STAGE_DECISION:
        stage = _text(event, "target_stage") or _text(event, "stage") or _text(event, "current_stage")
        if stage:
            view["stage"] = {"id": stage, "label": stage.replace("_", " ").title()}
        _set_role(view, "manager", "done", f"Stage · {stage}" if stage else "Stage reviewed", ts)
        _timeline(view, event, role="manager", title=f"Stage → {stage}" if stage else "Stage reviewed", detail=_text(event, "reason"))
        _role_work(
            view,
            event,
            role="manager",
            kind="stage_decision",
            title=f"Stage → {stage}" if stage else "Stage reviewed",
            detail=_text(event, "reason", 4000),
            status=_text(event, "action"),
        )


def reduce_planner_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type == EventType.LIFE_PLANNER_START:
        _set_role(view, "planner", "active", "Planning next work", ts)
        _role_work(
            view,
            event,
            role="planner",
            kind="planning",
            title="Planning next work",
            detail=_text(event, "objective", 4000),
            status="active",
        )

    elif event_type == EventType.LIFE_PLANNER_TASK_ADDED:
        item_id = _text(event, "item_id")
        deps = event.get("deps") if isinstance(event.get("deps"), list) else []
        _upsert(view.setdefault("dag", []), "id", item_id, {
            "id": item_id,
            "title": _text(event, "title", 240),
            "objective": _text(event, "objective", 1000),
            "status": "pending",
            "deps": [str(dep) for dep in deps if str(dep).strip()],
            "branch_id": _text(event, "branch_id") or item_id,
            "parent_branch_id": _text(event, "parent_branch_id") or None,
        })
        _set_role(view, "planner", "done", "Research branch added", ts)
        _timeline(view, event, role="planner", title="Research branch added", detail=_text(event, "title"), tone="info")
        _role_work(
            view,
            event,
            role="planner",
            kind="task",
            title=_text(event, "title", 240) or "Task added",
            detail=_text(event, "objective", 4000),
            status="pending",
        )

    elif event_type == EventType.LIFE_PLANNER_VERDICT:
        project_done = bool(event.get("project_done"))
        label = "Project reviewed" if project_done else "Planning complete"
        _set_role(view, "planner", "done", label, ts)
        _timeline(
            view,
            event,
            role="planner",
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
        _set_role(view, "planner", "waiting", "Waiting on external work", ts)
        _timeline(
            view,
            event,
            role="planner",
            title="Planner waiting",
            detail=_text(event, "reason") or _text(event, "waiting_reason"),
        )
        _role_work(
            view,
            event,
            role="planner",
            kind="waiting",
            title="Planner waiting",
            detail=_text(event, "reason", 4000)
            or _text(event, "waiting_reason", 4000),
            status="waiting",
        )

    elif event_type == EventType.LIFE_PLANNER_TERMINAL_IDLE:
        _set_role(view, "planner", "waiting", "Idle", ts)
        _timeline(
            view,
            event,
            role="planner",
            title="Planner idle",
            detail=_text(event, "reason"),
        )

    elif event_type == EventType.LIFE_PLANNER_ERROR:
        _set_role(view, "planner", "error", "Planning failed", ts)
        _timeline(
            view,
            event,
            role="planner",
            title="Planner failed",
            detail=_text(event, "error") or _text(event, "reason"),
            tone="error",
        )
