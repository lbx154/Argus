"""Versioned mission metadata and references for fresh agent sessions.

The packet indexes canonical sources without copying their prose:
``mission.json`` owns the task contract, ``CHECKPOINT.md`` owns durable state, and
the Reviewer verdict owns control.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping

CONTEXT_PACKET_VERSION = 3
CHECKPOINT_CONTRACT_VERSION = 2
CHECKPOINT_FILENAME = "CHECKPOINT.md"
HANDOFF_DIRNAME = "handoffs"
FRONTIER_FILENAME = "frontier.json"

log = logging.getLogger(__name__)


def _initialize_checkpoint(path: Path) -> bool:
    """Atomically create the mission's empty optional checkpoint placeholder.

    Empty is deliberate: round one should not pay checkpoint prompt overhead
    until a role actually has continuation state to preserve.  Exclusive create
    avoids overwriting a role-authored checkpoint when mission context is
    refreshed.  Failure is advisory because readers independently tolerate an
    absent or concurrently deleted checkpoint.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return True
    except OSError as exc:
        log.warning("could not initialize optional mission checkpoint %s: %s", path, exc)
        return False
    try:
        os.close(descriptor)
    except OSError as exc:
        log.warning("could not close initialized mission checkpoint %s: %s", path, exc)
        return False
    return True


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
        return {"path": ""}
    return {"path": str(path)}


def _model_visible_context_ref(ref: Mapping[str, Any]) -> dict[str, str]:
    """Drop host-only integrity metadata from agent-readable context packets."""
    hidden = {"content_hash", "hash", "sha", "sha256", "digest"}
    return {
        str(key): str(value)
        for key, value in ref.items()
        if str(key).strip().casefold() not in hidden
        and not str(key).strip().casefold().endswith("_sha256")
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def render_mission_contract(path: Path | str | None) -> str:
    """Render the canonical mission fields once for a fresh role turn."""
    if not path:
        return ""
    payload = _read_json_object(Path(path).expanduser())
    if str(payload.get("kind") or "") != "mission_context":
        return ""
    objective = str(payload.get("objective") or "").strip()
    if not objective:
        return ""
    lines = ["## Mission contract", objective]
    acceptance = str(payload.get("acceptance_check") or "").strip()
    if acceptance:
        lines.extend(("", "Acceptance:", acceptance))
    non_goals = [
        str(item).strip()
        for item in payload.get("non_goals") or []
        if str(item).strip()
    ]
    if non_goals:
        lines.extend(("", "Non-goals:", *(f"- {item}" for item in non_goals)))
    return "\n".join(lines)


def _brief_text(value: Any, *, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def _brief_items(value: Any, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        text
        for item in value
        if (text := _brief_text(item, limit=400))
    ))[:limit]


def _latest_reviewed_handoff(root: Path) -> dict[str, Any]:
    """Read the newest sealed Reviewer handoff, ignoring Engineer-only seals."""
    for path in reversed(sorted(root.glob("round-*.json"))):
        payload = _read_json_object(path)
        if str(payload.get("kind") or "") == "round_reviewed_handoff":
            return payload
    return {}


def render_mission_brief(path: Path | str | None) -> str:
    """Project canonical mission/frontier state into one compact role briefing.

    The projection selects named semantic fields only.  In particular it never
    reads a checkpoint or copies an Engineer transcript/summary, and it writes
    no state of its own.  Missing fields are omitted rather than inferred.
    """
    if not path:
        return ""
    mission_path = Path(path).expanduser()
    mission = _read_json_object(mission_path)
    if str(mission.get("kind") or "") != "mission_context":
        return ""

    frontier: dict[str, Any] = {}
    frontier_ref = mission.get("frontier")
    if isinstance(frontier_ref, Mapping):
        frontier_path = _brief_text(frontier_ref.get("path"), limit=4000)
        if frontier_path:
            frontier = _read_json_object(Path(frontier_path).expanduser())

    reviewed = _latest_reviewed_handoff(mission_path.parent)
    review = reviewed.get("review")
    review = review if isinstance(review, Mapping) else {}
    status = _brief_text(review.get("status"), limit=40)

    lines = ["## MissionBrief"]
    workdir = _brief_text(mission.get("execution_workdir"), limit=4000)
    stage = _brief_text(mission.get("stage"), limit=120)
    work_kind = _brief_text(mission.get("work_kind"), limit=80)
    acceptance = _brief_text(mission.get("acceptance_check"))
    if workdir:
        lines.append(f"- Workdir: `{workdir}`")
    if stage:
        lines.append(f"- Stage: {stage}")
    if work_kind:
        lines.append(f"- Work kind: {work_kind}")
    owns_paths = [
        str(item).strip()
        for item in (mission.get("owns_paths") or [])
        if str(item).strip()
    ]
    if owns_paths:
        lines.append(
            "- Owned paths (authoritative write boundary; Reviewer must not "
            "request edits outside it): " + "; ".join(owns_paths)
        )

    changed_surface = _brief_items(frontier.get("artifacts"))
    if changed_surface:
        lines.append("- Changed surface: " + "; ".join(changed_surface))

    resources: list[str] = []
    context_refs = mission.get("context_refs")
    if isinstance(context_refs, list):
        for ref in context_refs:
            if not isinstance(ref, Mapping):
                continue
            location = _brief_text(ref.get("ref"), limit=400)
            if not location:
                continue
            kind = _brief_text(ref.get("kind"), limit=80)
            why = _brief_text(ref.get("why"), limit=240)
            label = f"{kind}: {location}" if kind else location
            resources.append(f"{label} ({why})" if why else label)
    if resources:
        lines.append("- Tools/resources: " + "; ".join(resources[:6]))
    if acceptance:
        lines.append(f"- Native check: {acceptance}")

    reason = _brief_text(review.get("reason"))
    if reason:
        result = f"{status}: {reason}" if status else reason
        lines.append(f"- Decisive result: {result}")
    if reviewed and status != "done":
        missing = _brief_items(frontier.get("remaining_work"))
        if missing:
            lines.append("- Missing condition: " + "; ".join(missing))
    next_action = _brief_text(review.get("next_action"))
    if next_action:
        lines.append(f"- Next action: {next_action}")

    return "\n".join(lines) if len(lines) > 1 else ""


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
    work_kind: str = "scope",
    acceptance_check: str = "",
    plan_hypothesis: str = "",
    goal_contribution: str = "",
    expected_regressions: str = "",
    decision_rule: str = "",
    execution_workdir: str = "",
    owns_paths: list[str] | None = None,
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
    checkpoint_path = root / CHECKPOINT_FILENAME
    _initialize_checkpoint(checkpoint_path)
    existing_created_at = time.time()
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_created_at = float(existing.get("created_at") or existing_created_at)
    except (OSError, ValueError, TypeError):
        pass
    frontier_path = root / FRONTIER_FILENAME
    from ..core.task_frontier import (
        TaskFrontier,
        load_task_frontier,
        save_task_frontier,
    )

    frontier = load_task_frontier(frontier_path) or TaskFrontier.initial(
        mission_id=str(mission_id),
        objective=objective,
        invariants=[acceptance_check, *(non_goals or [])],
        hypothesis=plan_hypothesis,
        remaining_work=[acceptance_check] if acceptance_check else [],
        uncertainty="Unresolved until reviewed evidence narrows it.",
        next_decision_point=decision_rule,
    )
    save_task_frontier(frontier_path, frontier)
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "mission_context",
        "mission_id": str(mission_id),
        "stage": str(stage or ""),
        "scope": str(scope or ""),
        # The default parameter still types new callers as scope, while an
        # explicitly empty value preserves a pre-work-kind backlog mission.
        "work_kind": str(work_kind or "").strip(),
        "objective": str(objective or "").strip(),
        "acceptance_check": str(acceptance_check or "").strip(),
        "plan_hypothesis": str(plan_hypothesis or "").strip(),
        "goal_contribution": str(goal_contribution or "").strip(),
        "expected_regressions": str(expected_regressions or "").strip(),
        "decision_rule": str(decision_rule or "").strip(),
        "execution_workdir": str(execution_workdir or "").strip(),
        "owns_paths": [
            str(item).strip()
            for item in (owns_paths or [])
            if str(item).strip()
        ],
        "non_goals": [str(item).strip() for item in (non_goals or []) if str(item).strip()],
        "context_refs": [
            _model_visible_context_ref(ref)
            for ref in (context_refs or [])
            if isinstance(ref, dict) and str(ref.get("ref") or "").strip()
        ],
        "plan_id": str(plan_id or ""),
        "plan_version": max(0, int(plan_version or 0)),
        "node_key": str(node_key or ""),
        "deps": [str(dep) for dep in (deps or [])],
        "tags": [str(tag) for tag in (tags or [])],
        "frontier": _file_reference(frontier_path),
        "checkpoint": {
            **_file_reference(checkpoint_path),
            "contract_version": CHECKPOINT_CONTRACT_VERSION,
        },
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
        "frontier": _file_reference(root / FRONTIER_FILENAME),
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
    review_payload: dict[str, Any] = {
        "status": str(getattr(review, "status", "") or ""),
        "reason": str(getattr(review, "reason", "") or "")[:4000],
        "next_action": str(getattr(review, "next_action", "") or "")[:4000],
        "operator_question": str(getattr(review, "operator_question", "") or "")[:1000],
    }
    frontier_path = root / FRONTIER_FILENAME
    from ..core.task_frontier import load_task_frontier, save_task_frontier

    frontier = load_task_frontier(frontier_path)
    frontier_transition: dict[str, Any] = {}
    if frontier is not None:
        report = getattr(review, "frontier_report", {})
        if isinstance(report, dict):
            frontier_transition = frontier.apply(report, round_index=round_index)
            if frontier_transition:
                save_task_frontier(frontier_path, frontier)
                review_payload["frontier_transition"] = frontier_transition
                review_payload["frontier_disposition"] = frontier.disposition
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "round_reviewed_handoff",
        "mission_context": str(mission_path),
        "mission_id": root.name,
        "round": max(1, int(round_index)),
        "producer_role": "reviewer",
        "review": review_payload,
        "checkpoint": _file_reference(checkpoint_path),
        "frontier": _file_reference(frontier_path),
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
    "CHECKPOINT_CONTRACT_VERSION",
    "CHECKPOINT_FILENAME",
    "CONTEXT_PACKET_VERSION",
    "FRONTIER_FILENAME",
    "create_mission_context",
    "mission_context_dir",
    "record_engineer_handoff",
    "record_reviewed_handoff",
    "render_mission_brief",
    "render_mission_contract",
]
