"""Mission-view snapshot assembly: live daemon/session merge and disk bootstrap.

``snapshot_mission_view`` is the read path used by the webapi/cockpit: it
loads (or bootstraps from the JSONL event tail) the persisted event-sourced
view, merges in live, non-event-sourced session/daemon/backlog state that
does not go through the event log, and enriches learned-skill rows with
their current file content for display.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

from ._dispatch import _refresh_primary_metric, reduce_mission_view_event
from ._reduce_helpers import _number, _upsert
from ._view_state import (
    _PIPELINE_ROLE_NAMES,
    _ROLE_NAMES,
    MISSION_SKILL_CONTENT_MAX_BYTES,
    _locked,
    _read_unlocked,
    _tail_jsonl,
    _write_unlocked,
    empty_mission_view,
)


def _bootstrap_view(root: Path) -> dict[str, Any]:
    view = empty_mission_view()
    for path in (root / "events.jsonl.1", root / "events.jsonl"):
        for event in _tail_jsonl(path):
            reduce_mission_view_event(view, event)
    view["bootstrapped"] = True
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
    active_names = [
        str(role.get("role") or "")
        for role in roles
        if role.get("active") and str(role.get("role") or "") in _ROLE_NAMES
    ]
    if active_names:
        active_name = active_names[-1]
        for existing in role_rows:
            if (
                existing.get("role") in _PIPELINE_ROLE_NAMES
                and existing.get("role") != active_name
                and existing.get("status") == "active"
            ):
                existing.update({"status": "done", "label": "Handed off"})
        view["active_role"] = active_name
    else:
        for existing in role_rows:
            if existing.get("status") == "active":
                existing.update({"status": "waiting", "label": "Waiting"})
        view["active_role"] = ""
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
            "objective": str(item.get("objective") or ""),
            "status": str(item.get("status") or "pending"),
            "deps": [str(dep) for dep in (item.get("deps") or [])],
            "branch_id": item_id,
            "parent_branch_id": str((item.get("deps") or [""])[0] or "") or None,
            "acceptance_check": str(item.get("acceptance_check") or ""),
            "non_goals": [
                str(value) for value in (item.get("non_goals") or [])
            ],
        })

    now = time.time()
    campaign_started_at = (
        mission.get("campaign_started_at")
        or session.get("created")
        or mission.get("started_at")
    )
    if campaign_started_at:
        mission["campaign_started_at"] = float(campaign_started_at)
        mission["campaign_elapsed_seconds"] = max(
            0.0, now - float(campaign_started_at)
        )
    if mission.get("started_at") and mission.get("status") == "working":
        mission["elapsed_seconds"] = max(0.0, now - float(mission["started_at"]))
    elif mission.get("started_at") and mission.get("completed_at"):
        mission["elapsed_seconds"] = max(0.0, float(mission["completed_at"]) - float(mission["started_at"]))
    view["updated_at"] = now
    _refresh_primary_metric(view)
    return view


def _mission_for_timestamp(
    backlog: list[Mapping[str, Any]],
    timestamp: float,
) -> tuple[str, str]:
    candidates: list[Mapping[str, Any]] = []
    for item in backlog:
        started = _number(item, "started_ts")
        finished = _number(item, "finished_ts")
        if started is None or timestamp < started:
            continue
        if finished is not None and timestamp > finished + 5.0:
            continue
        candidates.append(item)
    if not candidates:
        return "", ""
    selected = max(
        candidates,
        key=lambda item: float(item.get("started_ts") or 0.0),
    )
    return (
        str(selected.get("id") or ""),
        str(selected.get("title") or "")[:240],
    )


def _discover_project_skills(
    root: Path,
    view: dict[str, Any],
    backlog: list[Mapping[str, Any]],
) -> None:
    skill_root = root / "skills"
    if not skill_root.is_dir():
        return
    from ...skills.store import Skill

    rows = view.setdefault("learned_skills", [])
    known_paths = {
        str(row.get("path") or "")
        for row in rows
        if isinstance(row, dict)
    }
    for path in sorted(skill_root.rglob("*.md")):
        if "_history" in path.parts or path.name.startswith("."):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            parsed = Skill.parse(text, str(path))
            modified = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        if parsed.protected or str(path) in known_paths:
            continue
        mission_id, mission_title = _mission_for_timestamp(backlog, modified)
        rows.append({
            "id": parsed.skill_id or path.stem,
            "name": parsed.name or path.stem,
            "version": parsed.version,
            "scope": "project",
            "path": str(path),
            "status": "active",
            "updated_at": modified,
            "mission_id": mission_id,
            "mission_title": mission_title,
        })


def _enrich_skill_content(
    root: Path,
    view: dict[str, Any],
    backlog: list[Mapping[str, Any]],
) -> None:
    _discover_project_skills(root, view, backlog)
    allowed_roots = [(root / "skills").resolve()]
    if root.parent.name == "projects":
        allowed_roots.append((root.parent.parent / "skills").resolve())
    for skill in view.setdefault("learned_skills", []):
        raw_path = str(skill.get("path") or skill.get("source_path") or "").strip()
        if not raw_path:
            continue
        candidates = [Path(raw_path).expanduser()]
        if not Path(raw_path).is_absolute():
            candidates.extend(base / raw_path for base in allowed_roots)
            candidates.append(root / raw_path)
        selected: Path | None = None
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not any(
                resolved == base or base in resolved.parents
                for base in allowed_roots
            ):
                continue
            if resolved.is_file() and resolved.suffix.lower() == ".md":
                selected = resolved
                break
        if selected is None:
            continue
        try:
            data = selected.read_bytes()
        except OSError:
            continue
        truncated = len(data) > MISSION_SKILL_CONTENT_MAX_BYTES
        skill["content"] = data[:MISSION_SKILL_CONTENT_MAX_BYTES].decode(
            "utf-8",
            errors="replace",
        )
        skill["content_truncated"] = truncated


def snapshot_mission_view(root: Path | str, **kwargs: Any) -> dict[str, Any]:
    path = Path(root).expanduser()
    with _locked(path):
        view = _read_unlocked(path)
        if not view.get("bootstrapped"):
            view = _bootstrap_view(path)
        view = merge_mission_view_snapshot(view, **kwargs)
        _write_unlocked(path, view)
        response = json.loads(json.dumps(view))
        _enrich_skill_content(
            path,
            response,
            list(kwargs.get("backlog") or []),
        )
        return response
