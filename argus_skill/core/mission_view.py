"""Durable event-sourced Mission View read model.

The reducer only consumes structured event fields. Free-form text may be shown
as detail, but it is never parsed to infer scientific state.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .event_catalog import EventType, canonical_event_type

MISSION_VIEW_FILE = "mission-view.json"
MISSION_VIEW_LOCK_FILE = "mission-view.lock"
MISSION_VIEW_SCHEMA_VERSION = 1
MISSION_TIMELINE_LIMIT = 120
MISSION_BOOTSTRAP_MAX_BYTES = 8 * 1024 * 1024

_ROLE_NAMES = ("manager", "planner", "engineer", "reviewer")
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def empty_mission_view() -> dict[str, Any]:
    return {
        "schema_version": MISSION_VIEW_SCHEMA_VERSION,
        "bootstrapped": False,
        "mission": {
            "id": "",
            "title": "",
            "objective": "",
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": 0.0,
        },
        "stage": {"id": "", "label": ""},
        "round": {"current": 0, "max": 0},
        "active_role": "",
        "roles": [
            {"role": role, "status": "waiting", "label": "Waiting", "updated_at": 0.0}
            for role in _ROLE_NAMES
        ],
        "dag": [],
        "hypotheses": [],
        "experiments": [],
        "metrics": [],
        "primary_metric": None,
        "timeline": [],
        "artifacts": [],
        "learned_skills": [],
        "storage": {
            "project_skill_dir": "",
            "global_skill_dir": "",
            "project_skill_count": 0,
            "global_skill_count": 0,
            "wiki_paths": [],
        },
        "achievement": None,
        "review": {"status": "", "reason": "", "rejected_attempts": 0},
        "last_event_ts": 0.0,
        "updated_at": 0.0,
    }


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / MISSION_VIEW_LOCK_FILE
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _read_unlocked(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / MISSION_VIEW_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        return empty_mission_view()
    if not isinstance(payload, dict) or payload.get("schema_version") != MISSION_VIEW_SCHEMA_VERSION:
        return empty_mission_view()
    payload.setdefault("storage", {
        "project_skill_dir": "",
        "global_skill_dir": "",
        "project_skill_count": 0,
        "global_skill_count": 0,
        "wiki_paths": [],
    })
    return payload


def load_mission_view(root: Path | str) -> dict[str, Any]:
    path = Path(root).expanduser()
    with _locked(path):
        return _read_unlocked(path)


def _write_unlocked(root: Path, view: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / MISSION_VIEW_FILE
    fd, tmp_name = tempfile.mkstemp(prefix=".mission-view-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(view, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


_PROJECTED_EVENT_TYPES = frozenset({
    EventType.LIFE_MANAGER_INTENT_COMPLETED,
    EventType.LIFE_MANAGER_STAGE_DECISION,
    EventType.LIFE_PLANNER_START,
    EventType.LIFE_PLANNER_TASK_ADDED,
    EventType.LIFE_MISSION_STARTED,
    EventType.LIFE_MISSION_COMPLETED,
    EventType.LIFE_MISSION_FAILED,
    EventType.ROUND_START,
    EventType.ROUND_REVIEW_STARTED,
    EventType.ROUND_REVIEW_COMPLETED,
    EventType.ENGINEER_PROGRESS,
    EventType.RESEARCH_HYPOTHESIS_PROPOSED,
    EventType.RESEARCH_EXPERIMENT_STARTED,
    EventType.RESEARCH_EXPERIMENT_COMPLETED,
    EventType.RESEARCH_METRIC_REPORTED,
    EventType.RESEARCH_METRIC_VERIFIED,
    EventType.RESEARCH_ARTIFACT_REGISTERED,
    EventType.RESEARCH_ACHIEVEMENT_CERTIFIED,
    EventType.SKILL_CREATED,
    EventType.SKILL_UPDATED,
    EventType.SKILL_ARCHIVED,
    EventType.SKILL_EVOLUTION_COMPLETED,
    EventType.WIKI_INITIALIZED,
    EventType.WIKI_EVOLUTION_COMPLETED,
})


def _tail_jsonl(path: Path, max_bytes: int = MISSION_BOOTSTRAP_MAX_BYTES) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            handle.seek(start)
            raw = handle.read()
    except OSError:
        return []
    if start:
        _discard, separator, raw = raw.partition(b"\n")
        if not separator:
            return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        if canonical_event_type(event.get("type")) not in _PROJECTED_EVENT_TYPES:
            continue
        rows.append(event)
    return rows


def _bootstrap_view(root: Path) -> dict[str, Any]:
    view = empty_mission_view()
    for path in (root / "events.jsonl.1", root / "events.jsonl"):
        for event in _tail_jsonl(path):
            reduce_mission_view_event(view, event)
    view["bootstrapped"] = True
    return view


def mission_view_handles_event(event_type: Any) -> bool:
    return canonical_event_type(event_type) in _PROJECTED_EVENT_TYPES


def _text(event: Mapping[str, Any], key: str, limit: int = 500) -> str:
    return str(event.get(key) or "").strip()[:limit]


def _number(event: Mapping[str, Any], key: str) -> float | None:
    value = event.get(key)
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _integer(event: Mapping[str, Any], key: str) -> int | None:
    value = _number(event, key)
    return int(value) if value is not None else None


def _event_id(event: Mapping[str, Any]) -> str:
    explicit = event.get("event_id") or event.get("id")
    if explicit:
        return str(explicit)
    stable = json.dumps(dict(event), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]


def _upsert(rows: list[dict[str, Any]], key: str, value: str, patch: dict[str, Any]) -> None:
    if not value:
        return
    for index, row in enumerate(rows):
        if str(row.get(key) or "") == value:
            rows[index] = {**row, **patch}
            return
    rows.append(patch)


def _set_role(view: dict[str, Any], role: str, status: str, label: str, ts: float) -> None:
    if role not in _ROLE_NAMES:
        return
    roles = view.setdefault("roles", [])
    patch = {"role": role, "status": status, "label": label, "updated_at": ts}
    _upsert(roles, "role", role, patch)
    if status == "active":
        view["active_role"] = role


def _timeline(
    view: dict[str, Any],
    event: Mapping[str, Any],
    *,
    role: str,
    title: str,
    detail: str = "",
    tone: str = "neutral",
) -> None:
    rows = view.setdefault("timeline", [])
    event_id = _event_id(event)
    if any(str(row.get("id") or "") == event_id for row in rows):
        return
    row = {
        "id": event_id,
        "ts": float(event.get("ts") or time.time()),
        "type": canonical_event_type(event.get("type")),
        "role": role,
        "title": title[:180],
        "detail": detail[:500],
        "tone": tone,
    }
    for key in ("item_id", "branch_id", "hypothesis_id", "experiment_id", "metric_id"):
        value = _text(event, key, 160)
        if value:
            row[key] = value
    rows.append(row)
    view["timeline"] = rows[-MISSION_TIMELINE_LIMIT:]


_PROGRESS_LABELS = {
    "agent_message": "Reporting progress",
    "assistant_message": "Reporting progress",
    "command_execution": "Running a command",
    "reasoning": "Reasoning",
    "tool_use": "Using a tool",
    "tool_result": "Inspecting tool output",
    "codex_idle": "Waiting for model output",
}


def reduce_mission_view_event(view: dict[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    event_type = canonical_event_type(event.get("type"))
    ts = float(event.get("ts") or time.time())
    view["last_event_ts"] = max(float(view.get("last_event_ts") or 0.0), ts)
    mission = view.setdefault("mission", {})

    if event_type == EventType.LIFE_MANAGER_INTENT_COMPLETED:
        mission.update({
            "id": _text(event, "item_id"),
            "title": _text(event, "objective", 180),
            "objective": _text(event, "objective", 2000),
            "status": "framed",
        })
        stages = event.get("stages")
        if isinstance(stages, list) and stages and not _text(view.get("stage", {}), "id"):
            stage = str(stages[0] or "").strip()
            view["stage"] = {"id": stage, "label": stage.replace("_", " ").title()}
        _set_role(view, "manager", "done", "Goal framed", ts)
        _timeline(view, event, role="manager", title="Goal framed", detail=_text(event, "reason"), tone="success")

    elif event_type == EventType.LIFE_MANAGER_STAGE_DECISION:
        stage = _text(event, "target_stage") or _text(event, "stage") or _text(event, "current_stage")
        if stage:
            view["stage"] = {"id": stage, "label": stage.replace("_", " ").title()}
        _set_role(view, "manager", "done", f"Stage · {stage}" if stage else "Stage reviewed", ts)
        _timeline(view, event, role="manager", title=f"Stage → {stage}" if stage else "Stage reviewed", detail=_text(event, "reason"))

    elif event_type == EventType.LIFE_PLANNER_START:
        _set_role(view, "planner", "active", "Planning next work", ts)

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

    elif event_type == EventType.LIFE_MISSION_STARTED:
        mission.update({
            "id": _text(event, "item_id"),
            "title": _text(event, "title", 240),
            "objective": _text(event, "objective", 2000),
            "status": "working",
            "started_at": ts,
            "completed_at": None,
        })
        _set_role(view, "engineer", "active", "Starting mission", ts)
        _timeline(view, event, role="engineer", title="Mission started", detail=_text(event, "title"), tone="info")

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
        if kind not in {"reasoning", "assistant_message", "agent_message"}:
            _timeline(view, event, role=role, title=label, detail=_text(event, "action_summary") or _text(event, "text"))

    elif event_type == EventType.ROUND_REVIEW_STARTED:
        _set_role(view, "reviewer", "active", "Reviewing benchmark evidence", ts)

    elif event_type == EventType.ROUND_REVIEW_COMPLETED:
        status = _text(event, "status")
        reason = _text(event, "reason")
        view["review"] = {
            "status": status,
            "reason": reason,
            "rejected_attempts": int(view.get("review", {}).get("rejected_attempts") or 0)
            + (1 if status in {"continue", "blocked"} else 0),
        }
        _set_role(view, "reviewer", "done" if status == "done" else "rejected", "Accepted evidence" if status == "done" else "Requested another attempt", ts)
        _timeline(
            view,
            event,
            role="reviewer",
            title="Evidence accepted" if status == "done" else "Attempt rejected",
            detail=reason,
            tone="success" if status == "done" else "error",
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

    elif event_type == EventType.RESEARCH_HYPOTHESIS_PROPOSED:
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

    elif event_type in {EventType.SKILL_CREATED, EventType.SKILL_UPDATED}:
        skill_id = _text(event, "skill_id") or _text(event, "name")
        if skill_id:
            _upsert(view.setdefault("learned_skills", []), "id", skill_id, {
                "id": skill_id,
                "name": _text(event, "name", 240),
                "version": _integer(event, "version") or 1,
                "scope": _text(event, "scope"),
                "path": _text(event, "path", 500),
                "status": "active",
                "updated_at": ts,
            })
            _timeline(view, event, role="reviewer", title="Capability unlocked" if event_type == EventType.SKILL_CREATED else "Capability upgraded", detail=_text(event, "name"), tone="skill")

    elif event_type == EventType.SKILL_ARCHIVED:
        skill_id = _text(event, "skill_id") or _text(event, "name")
        for skill in view.setdefault("learned_skills", []):
            if skill.get("id") == skill_id:
                skill.update({"status": "archived", "updated_at": ts})

    elif event_type == EventType.SKILL_EVOLUTION_COMPLETED:
        storage = view.setdefault("storage", {})
        for key in ("project_skill_dir", "global_skill_dir"):
            value = _text(event, key, 1000)
            if value:
                storage[key] = value
        for key in ("project_skill_count", "global_skill_count"):
            storage[key] = _integer(event, key)

    elif event_type in {
        EventType.WIKI_INITIALIZED,
        EventType.WIKI_EVOLUTION_COMPLETED,
    }:
        storage = view.setdefault("storage", {})
        paths = [str(path) for path in storage.setdefault("wiki_paths", []) if path]
        candidates = list(event.get("paths") or [])
        path = _text(event, "path", 1000)
        if path:
            candidates.append(path)
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value and value not in paths:
                paths.append(value)
        storage["wiki_paths"] = paths

    elif event_type == EventType.RESEARCH_ACHIEVEMENT_CERTIFIED:
        view["achievement"] = {
            "id": _text(event, "achievement_id"),
            "title": _text(event, "title", 240),
            "goal": _text(event, "goal", 2000),
            "summary": _text(event, "summary", 2000),
            "metric_id": _text(event, "metric_id"),
            "evidence": list(event.get("evidence") or []),
            "reviewer_certified": True,
            "certified_at": ts,
        }

    elif event_type in {EventType.LIFE_MISSION_COMPLETED, EventType.LIFE_MISSION_FAILED}:
        success = bool(event.get("success")) and event_type == EventType.LIFE_MISSION_COMPLETED
        mission.update({
            "id": _text(event, "item_id") or mission.get("id", ""),
            "title": _text(event, "title", 240) or mission.get("title", ""),
            "objective": _text(event, "objective", 2000) or mission.get("objective", ""),
            "status": "complete" if success else _text(event, "status") or "failed",
            "completed_at": ts,
        })
        _set_role(view, "engineer", "done" if success else "error", "Mission complete" if success else "Mission failed", ts)
        _timeline(view, event, role="engineer", title="Mission achievement" if success else "Mission failed", detail=_text(event, "title") or _text(event, "status"), tone="success" if success else "error")

    _refresh_primary_metric(view)
    _refresh_achievement(view)
    view["updated_at"] = time.time()
    return view


def _refresh_primary_metric(view: dict[str, Any]) -> None:
    metrics = [metric for metric in view.get("metrics", []) if metric.get("value") is not None]
    if not metrics:
        view["primary_metric"] = None
        return
    primary = [metric for metric in metrics if metric.get("primary")]
    candidates = primary or metrics
    accepted = [metric for metric in candidates if metric.get("verification_status") == "accepted"]
    candidates = accepted or candidates
    metric_name = str(candidates[-1].get("name") or "")
    same = [metric for metric in candidates if str(metric.get("name") or "") == metric_name]
    direction = str(same[-1].get("direction") or "maximize")
    if direction == "minimize":
        best = min(same, key=lambda metric: float(metric.get("value")))
    elif direction == "target":
        best = same[-1]
    else:
        best = max(same, key=lambda metric: float(metric.get("value")))
    view["primary_metric"] = dict(best)


def _refresh_achievement(view: dict[str, Any]) -> None:
    mission = view.get("mission", {})
    metric = view.get("primary_metric")
    if mission.get("status") != "complete" or not metric:
        return
    if metric.get("verification_status") != "accepted":
        return
    if view.get("achievement") and view["achievement"].get("reviewer_certified"):
        return
    started = mission.get("started_at")
    completed = mission.get("completed_at")
    elapsed = max(0.0, float(completed) - float(started)) if started and completed else 0.0
    baseline = metric.get("baseline")
    value = metric.get("value")
    gain = (float(value) - float(baseline)) if value is not None and baseline is not None else None
    view["achievement"] = {
        "id": f"derived-{mission.get('id') or 'mission'}",
        "title": mission.get("title") or "Argus achievement",
        "goal": mission.get("objective") or mission.get("title") or "",
        "metric_id": metric.get("id"),
        "metric_name": metric.get("name"),
        "baseline": baseline,
        "best": value,
        "gain": gain,
        "unit": metric.get("unit") or "",
        "experiments_run": sum(1 for row in view.get("experiments", []) if row.get("status") == "completed"),
        "rejected_attempts": int(view.get("review", {}).get("rejected_attempts") or 0),
        "skills_learned": sum(1 for row in view.get("learned_skills", []) if row.get("status") == "active"),
        "artifacts": len(view.get("artifacts", [])),
        "elapsed_seconds": elapsed,
        "reviewer_certified": True,
        "certified_at": completed,
    }


def update_mission_view_event(root: Path | str, event: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(root).expanduser()
    if not mission_view_handles_event(event.get("type")):
        return load_mission_view(path)
    with _locked(path):
        view = reduce_mission_view_event(_read_unlocked(path), event)
        _write_unlocked(path, view)
        return view


def merge_mission_view_snapshot(
    view: dict[str, Any],
    *,
    session: Mapping[str, Any],
    daemon: Mapping[str, Any],
    roles: list[Mapping[str, Any]],
    backlog: list[Mapping[str, Any]],
    continuous: Mapping[str, Any] | None = None,
    current_stage: str = "",
) -> dict[str, Any]:
    mission = view.setdefault("mission", {})
    active = next((item for item in backlog if str(item.get("status")) in {"running", "in_progress", "claimed"}), None)
    queued = next((item for item in backlog if str(item.get("status")) == "pending"), None)
    objective = str(
        (continuous or {}).get("objective")
        or session.get("objective")
        or (active or {}).get("objective")
        or (active or {}).get("title")
        or (queued or {}).get("objective")
        or (queued or {}).get("title")
        or mission.get("objective")
        or ""
    ).strip()
    if objective:
        mission["objective"] = objective
        mission["title"] = mission.get("title") or objective.splitlines()[0][:240]
    if active:
        mission["id"] = str(active.get("id") or mission.get("id") or "")
        mission["status"] = "working"
        mission["started_at"] = mission.get("started_at") or active.get("started_ts")
    elif (continuous or {}).get("done_reason") or (continuous or {}).get("done_at"):
        mission["status"] = "complete"
    elif queued or (continuous or {}).get("enabled"):
        mission["status"] = "queued"
    elif daemon.get("alive"):
        mission["status"] = "idle"
    has_mission_context = bool(
        objective
        or active
        or queued
        or (continuous or {}).get("enabled")
        or (continuous or {}).get("done_reason")
        or (continuous or {}).get("done_at")
        or mission.get("id")
    )
    if current_stage and has_mission_context:
        view["stage"] = {"id": current_stage, "label": current_stage.replace("_", " ").title()}
    elif not has_mission_context:
        view["stage"] = {"id": "", "label": ""}

    role_rows = view.setdefault("roles", [])
    for role in roles:
        name = str(role.get("role") or "")
        if name not in _ROLE_NAMES:
            continue
        if role.get("active"):
            patch = {
                "role": name,
                "status": "active",
                "label": str(role.get("label") or role.get("status") or "Working"),
                "updated_at": time.time() - float(role.get("age_s") or 0.0),
                "backend": str(role.get("backend") or ""),
                "model": str(role.get("model") or ""),
                "effort": role.get("effort"),
            }
            _upsert(role_rows, "role", name, patch)
            view["active_role"] = name
        else:
            for existing in role_rows:
                if existing.get("role") == name:
                    existing.update({
                        "backend": str(role.get("backend") or ""),
                        "model": str(role.get("model") or ""),
                        "effort": role.get("effort"),
                    })
                    break

    dag = view.setdefault("dag", [])
    for item in backlog:
        item_id = str(item.get("id") or "")
        _upsert(dag, "id", item_id, {
            "id": item_id,
            "title": str(item.get("title") or "")[:240],
            "objective": str(item.get("objective") or "")[:1000],
            "status": str(item.get("status") or "pending"),
            "deps": [str(dep) for dep in (item.get("deps") or [])],
            "branch_id": item_id,
            "parent_branch_id": str((item.get("deps") or [""])[0] or "") or None,
        })

    now = time.time()
    if mission.get("started_at") and mission.get("status") == "working":
        mission["elapsed_seconds"] = max(0.0, now - float(mission["started_at"]))
    elif mission.get("started_at") and mission.get("completed_at"):
        mission["elapsed_seconds"] = max(0.0, float(mission["completed_at"]) - float(mission["started_at"]))
    view["updated_at"] = now
    _refresh_primary_metric(view)
    _refresh_achievement(view)
    return view


def snapshot_mission_view(root: Path | str, **kwargs: Any) -> dict[str, Any]:
    path = Path(root).expanduser()
    with _locked(path):
        view = _read_unlocked(path)
        if not view.get("bootstrapped"):
            view = _bootstrap_view(path)
        view = merge_mission_view_snapshot(view, **kwargs)
        _write_unlocked(path, view)
        return view


__all__ = [
    "MISSION_VIEW_FILE",
    "MISSION_VIEW_SCHEMA_VERSION",
    "empty_mission_view",
    "load_mission_view",
    "merge_mission_view_snapshot",
    "mission_view_handles_event",
    "reduce_mission_view_event",
    "snapshot_mission_view",
    "update_mission_view_event",
]
