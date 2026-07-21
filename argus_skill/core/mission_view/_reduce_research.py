"""Research-evidence mission-view event-family reducers.

Covers the hypothesis -> experiment -> metric -> artifact chain that the
Engineer emits during a mission, plus the final achievement certification.
All numeric/verification judgement (accepted vs rejected, primary metric
choice, gain computation) already happened upstream (Reviewer / Engineer);
this module only projects those structured fields.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..event_catalog import EventType
from ._reduce_helpers import _integer, _number, _set_role, _text, _timeline, _upsert


def reduce_research_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    if event_type == EventType.RESEARCH_HYPOTHESIS_PROPOSED:
        hypothesis_id = _text(event, "hypothesis_id")
        _upsert(view.setdefault("hypotheses", []), "id", hypothesis_id, {
            "id": hypothesis_id,
            "title": _text(event, "title", 240),
            "statement": _text(event, "statement", 2000),
            "branch_id": _text(event, "branch_id"),
            "parent_branch_id": _text(event, "parent_branch_id") or None,
            "status": "proposed",
            "ts": ts,
        })
        _timeline(view, event, role="engineer", title="Hypothesis proposed", detail=_text(event, "title"), tone="info")

    elif event_type == EventType.RESEARCH_EXPERIMENT_STARTED:
        experiment_id = _text(event, "experiment_id")
        _upsert(view.setdefault("experiments", []), "id", experiment_id, {
            "id": experiment_id,
            "title": _text(event, "title", 240),
            "status": "running",
            "hypothesis_id": _text(event, "hypothesis_id"),
            "branch_id": _text(event, "branch_id"),
            "started_at": ts,
            "completed_at": None,
            "summary": _text(event, "summary"),
        })
        _set_role(view, "engineer", "active", f"Running {_text(event, 'title', 120)}", ts)
        _timeline(view, event, role="engineer", title="Experiment started", detail=_text(event, "title"), tone="info")

    elif event_type == EventType.RESEARCH_EXPERIMENT_COMPLETED:
        experiment_id = _text(event, "experiment_id")
        status = _text(event, "status")
        _upsert(view.setdefault("experiments", []), "id", experiment_id, {
            "id": experiment_id,
            "status": status,
            "hypothesis_id": _text(event, "hypothesis_id"),
            "branch_id": _text(event, "branch_id"),
            "completed_at": ts,
            "duration_seconds": _number(event, "duration_seconds"),
            "summary": _text(event, "summary"),
            "evidence": list(event.get("evidence") or []),
        })
        _timeline(view, event, role="engineer", title=f"Experiment {status}", detail=_text(event, "summary"), tone="success" if status == "completed" else "error")

    elif event_type == EventType.RESEARCH_METRIC_REPORTED:
        metric_id = _text(event, "metric_id")
        _upsert(view.setdefault("metrics", []), "id", metric_id, {
            "id": metric_id,
            "name": _text(event, "name", 120),
            "baseline": _number(event, "baseline"),
            "value": _number(event, "value"),
            "unit": _text(event, "unit", 32),
            "direction": _text(event, "direction"),
            "evidence": _text(event, "evidence", 500),
            "experiment_id": _text(event, "experiment_id"),
            "hypothesis_id": _text(event, "hypothesis_id"),
            "branch_id": _text(event, "branch_id"),
            "round_index": _integer(event, "round_index"),
            "primary": bool(event.get("primary")),
            "verification_status": "reported",
            "reported_at": ts,
        })
        _timeline(view, event, role="engineer", title="Metric reported", detail=f"{_text(event, 'name')} = {event.get('value')}{_text(event, 'unit', 32)}", tone="metric")

    elif event_type == EventType.RESEARCH_METRIC_VERIFIED:
        metric_id = _text(event, "metric_id")
        for metric in view.setdefault("metrics", []):
            if metric.get("id") == metric_id:
                metric.update({
                    "verification_status": _text(event, "status"),
                    "reviewer_reason": _text(event, "reviewer_reason"),
                    "verified_at": ts,
                    "verification_source": "research.metric.verified",
                })
                break
        accepted = _text(event, "status") == "accepted"
        _timeline(view, event, role="reviewer", title="Metric verified" if accepted else "Metric rejected", detail=_text(event, "reviewer_reason"), tone="success" if accepted else "error")

    elif event_type == EventType.RESEARCH_ARTIFACT_REGISTERED:
        artifact_id = _text(event, "artifact_id")
        _upsert(view.setdefault("artifacts", []), "id", artifact_id, {
            "id": artifact_id,
            "path": _text(event, "path", 500),
            "kind": _text(event, "kind"),
            "title": _text(event, "title", 240),
            "why": _text(event, "why", 500),
            "experiment_id": _text(event, "experiment_id"),
            "branch_id": _text(event, "branch_id"),
            "registered_at": ts,
        })
        _timeline(view, event, role="engineer", title="Artifact registered", detail=_text(event, "path", 300), tone="info")


def reduce_achievement_event(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    event_type: str,
    ts: float,
    mission: dict[str, Any],
) -> None:
    metric_id = _text(event, "metric_id")
    metric = next(
        (
            row
            for row in view.get("metrics", [])
            if str(row.get("id") or "") == metric_id
        ),
        None,
    )
    baseline = metric.get("baseline") if metric else None
    metric_value = metric.get("value") if metric else None
    mission = view.get("mission", {})
    started = mission.get("started_at")
    completed = mission.get("completed_at")
    elapsed = (
        max(0.0, float(completed) - float(started))
        if started and completed
        else 0.0
    )
    view["achievement"] = {
        "id": _text(event, "achievement_id"),
        "title": _text(event, "title", 240),
        "goal": _text(event, "goal", 2000),
        "summary": _text(event, "summary", 2000),
        "metric_id": metric_id,
        "metric_name": metric.get("name") if metric else "",
        "baseline": baseline,
        "best": metric_value,
        "gain": (
            float(metric_value) - float(baseline)
            if metric_value is not None and baseline is not None
            else None
        ),
        "unit": metric.get("unit") if metric else "",
        "experiments_run": sum(
            1
            for row in view.get("experiments", [])
            if row.get("status") == "completed"
        ),
        "rejected_attempts": int(
            view.get("review", {}).get("rejected_attempts") or 0
        ),
        "skills_learned": sum(
            1
            for row in view.get("learned_skills", [])
            if row.get("status") == "active"
        ),
        "artifacts": len(view.get("artifacts", [])),
        "elapsed_seconds": elapsed,
        "evidence": list(event.get("evidence") or []),
        "reviewer_certified": True,
        "certified_at": ts,
    }
