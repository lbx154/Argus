"""Versioned mission metadata and references for fresh agent sessions.

The packet indexes canonical sources without copying their prose:
``mission.json`` owns the task contract, ``CHECKPOINT.md`` owns durable state, and
the Reviewer verdict owns control.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from ..core.models import canonical_planner_report

CONTEXT_PACKET_VERSION = 2
HANDOFF_DIRNAME = "handoffs"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _file_reference(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": "", "sha256": ""}
    try:
        raw = path.read_bytes()
    except OSError:
        return {"path": str(path), "sha256": ""}
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _attach_mission_metadata(
    mission_path: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a round handoff with one reference to the mission contract."""
    out = dict(payload)
    out["mission"] = {"path": str(mission_path)}
    return out


def _latest_handoff_reference(
    *,
    mission_path: Path,
    handoff_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "handoff_ref",
        "mission": {"path": str(mission_path)},
        "handoff": _file_reference(handoff_path),
    }


def mission_context_dir(life_dir: Path | str, mission_id: str) -> Path:
    return Path(life_dir).expanduser() / HANDOFF_DIRNAME / str(mission_id)


def create_mission_context(
    *,
    life_dir: Path | str,
    mission_id: str,
    stage: str,
    objective: str,
    scope: str = "",
    acceptance_check: str = "",
    non_goals: list[str] | None = None,
    context_refs: list[dict[str, str]] | None = None,
    plan_id: str = "",
    plan_version: int = 0,
    node_key: str = "",
    deps: list[str] | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Create or refresh the immutable mission-level handoff description."""
    root = mission_context_dir(life_dir, mission_id)
    path = root / "mission.json"
    existing_created_at = time.time()
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_created_at = float(existing.get("created_at") or existing_created_at)
    except (OSError, ValueError, TypeError):
        pass
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "mission_context",
        "mission_id": str(mission_id),
        "stage": str(stage or ""),
        "scope": str(scope or ""),
        "objective": str(objective or "").strip(),
        "acceptance_check": str(acceptance_check or "").strip(),
        "non_goals": [
            str(item).strip()
            for item in (non_goals or [])
            if str(item).strip()
        ],
        "context_refs": [
            {str(key): str(value) for key, value in ref.items()}
            for ref in (context_refs or [])
            if isinstance(ref, dict) and str(ref.get("ref") or "").strip()
        ],
        "plan_id": str(plan_id or ""),
        "plan_version": max(0, int(plan_version or 0)),
        "node_key": str(node_key or ""),
        "deps": [str(dep) for dep in (deps or [])],
        "tags": [str(tag) for tag in (tags or [])],
        "created_at": existing_created_at,
        "updated_at": time.time(),
    }
    _atomic_write_json(path, payload)
    latest_path = root / "latest.json"
    if not latest_path.exists():
        _atomic_write_json(root / "latest.json", payload)
    else:
        latest = _read_json_object(latest_path)
        if str(latest.get("kind") or "") != "mission_context":
            _atomic_write_json(latest_path, _attach_mission_metadata(path, latest))
    return path


def record_engineer_handoff(
    *,
    mission_context_path: Path | str | None,
    round_index: int,
    engineer_summary: str,
    checkpoint_path: Path | None,
    control_path: Path | None = None,
    thread_id: str = "",
) -> Path | None:
    if not mission_context_path:
        return None
    mission_path = Path(mission_context_path)
    root = mission_path.parent
    _ = engineer_summary
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "round_engineer_handoff",
        "mission_context": str(mission_path),
        "mission_id": root.name,
        "round": max(1, int(round_index)),
        "producer_role": "engineer",
        "session_id": str(thread_id or ""),
        "checkpoint": _file_reference(checkpoint_path),
        "control": _file_reference(control_path),
        "created_at": time.time(),
    }
    path = root / f"round-{max(1, int(round_index)):04d}-engineer.json"
    _atomic_write_json(path, payload)
    _atomic_write_json(
        root / "latest.json",
        _latest_handoff_reference(
            mission_path=mission_path,
            handoff_path=path,
        ),
    )
    return path


def record_reviewed_handoff(
    *,
    mission_context_path: Path | str | None,
    round_index: int,
    engineer_summary: str,
    review: Any,
    checkpoint_path: Path | None,
) -> Path | None:
    if not mission_context_path:
        return None
    mission_path = Path(mission_context_path)
    root = mission_path.parent
    _ = engineer_summary
    planner_report = canonical_planner_report(
        getattr(review, "planner_report", None)
    )
    review_payload: dict[str, Any] = {
        "status": str(getattr(review, "status", "") or ""),
        "reason": str(getattr(review, "reason", "") or "")[:4000],
        "next_action": str(getattr(review, "next_action", "") or "")[:4000],
        "progress_class": str(getattr(review, "progress_class", "") or ""),
        "failure_cause": str(getattr(review, "failure_cause", "") or ""),
        "failure_layer": str(getattr(review, "failure_layer", "") or ""),
        "planner_report": planner_report,
        "harness_control": dict(
            getattr(review, "harness_control", {}) or {}
        ),
    }
    checklist = getattr(review, "checklist", None)
    if isinstance(checklist, list) and checklist:
        review_payload["checklist"] = list(checklist)
    certification_payload = getattr(review, "certification_payload", None)
    if isinstance(certification_payload, dict) and certification_payload:
        review_payload["certification_payload"] = dict(certification_payload)
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "round_reviewed_handoff",
        "mission_context": str(mission_path),
        "mission_id": root.name,
        "round": max(1, int(round_index)),
        "producer_role": "reviewer",
        "review": review_payload,
        "checkpoint": _file_reference(checkpoint_path),
        "created_at": time.time(),
    }
    path = root / f"round-{max(1, int(round_index)):04d}.json"
    _atomic_write_json(path, payload)
    _atomic_write_json(
        root / "latest.json",
        _latest_handoff_reference(
            mission_path=mission_path,
            handoff_path=path,
        ),
    )
    return path


__all__ = [
    "CONTEXT_PACKET_VERSION",
    "create_mission_context",
    "mission_context_dir",
    "record_engineer_handoff",
    "record_reviewed_handoff",
]
