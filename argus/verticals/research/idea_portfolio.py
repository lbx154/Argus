"""Carry an idea selection made under the legacy ``research/`` layout forward.

The runtime no longer forms idea portfolios; the Idea stage is the agents' own
work. A project that finished a selection before pipeline state recorded it
still has it moved into ``selected_idea`` once, so the campaign does not lose it.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ...core.file_lock import exclusive_file_lock
from ...core.pipeline_state import read_pipeline_state, write_pipeline_state

SELECTION_POLICY = "fixed_twelve_source_only_v6"
_LEGACY_PORTFOLIO_SIZE = 12
_SELECTION_SCHEMA_VERSION = 3
_STATE_LOCK_PATH = Path(".argus") / "IDEA_PORTFOLIO.lock"
_LEGACY_SELECTION_PATH = Path("research") / "IDEA_SELECTION.json"
_UNSELECTED = "not selected by the authoritative portfolio comparison"


def _resolved_roots(
    project_root: Path,
    state_root: Path | None,
) -> tuple[Path, Path]:
    project = Path(project_root).expanduser().resolve()
    state = Path(state_root or project).expanduser().resolve()
    return project, state


@contextmanager
def _state_lock(state_root: Path) -> Iterator[None]:
    path = state_root / _STATE_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        with exclusive_file_lock(handle, lock_name="idea portfolio state"):
            yield


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def _valid_selected_idea(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    required = ("route_id", "rationale", "resource_requirements", "rejections")
    if any(not payload.get(key) for key in required):
        return None
    rejections = payload.get("rejections")
    if not isinstance(rejections, dict):
        return None
    route_id = _one_line(payload.get("route_id"))
    normalized = {
        _one_line(key): _one_line(value)
        for key, value in rejections.items()
        if _one_line(key) != route_id and _one_line(value)
    }
    if not normalized:
        return None
    risks = payload.get("unresolved_risks")
    compact: dict[str, Any] = {
        "schema_version": _SELECTION_SCHEMA_VERSION,
        "policy": SELECTION_POLICY,
        "route_id": route_id,
        **{
            key: _one_line(payload.get(key))
            for key in (
                "route_task_id",
                "review_task_id",
                "route_artifact",
                "review_artifact",
                "rationale",
                "evidence_considered",
                "resource_requirements",
            )
        },
        "unresolved_risks": [
            _one_line(item)
            for item in (risks if isinstance(risks, list) else ())
            if _one_line(item)
        ],
        "rejections": normalized,
    }
    if payload.get("migrated_from_legacy") is True:
        compact["migrated_from_legacy"] = True
    return compact


def _write_handoff(project_root: Path, selection: dict[str, Any]) -> None:
    from ...core.atomic_replace import write_atomic

    selection = _valid_selected_idea(selection) or {}
    rejections = selection.get("rejections")
    rejection_lines = "\n".join(
        f"- **{route_id}**: {_one_line(reason)}"
        for route_id, reason in sorted(
            rejections.items() if isinstance(rejections, dict) else ()
        )
    )
    unresolved = selection.get("unresolved_risks")
    unresolved_lines = "\n".join(
        f"- {_one_line(item)}"
        for item in (unresolved if isinstance(unresolved, list) else ())
        if _one_line(item)
    ) or "- None recorded at selection."
    from .notes import notes_heading, research_notes_path

    text = (
        f"{notes_heading('idea')}\n\n"
        "## Selected idea\n"
        f"- Route: `{selection.get('route_id')}`\n"
        f"- Why it won: {selection.get('rationale')}\n"
        f"- Evidence considered: {selection.get('evidence_considered')}\n"
        f"- Resource needs: {selection.get('resource_requirements')}\n\n"
        "## Unresolved implementation obligations\n"
        f"{unresolved_lines}\n\n"
        "## Rejected routes\n"
        f"{rejection_lines}\n"
    )
    path = research_notes_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, text)


def _legacy_selection(project_root: Path) -> dict[str, Any] | None:
    source = _json_object(project_root / _LEGACY_SELECTION_PATH)
    if source is None:
        return None
    route_id = _one_line(source.get("route_id"))
    if not route_id:
        return None
    others = sorted(
        candidate
        for candidate in (
            f"route-{index:02d}" for index in range(1, _LEGACY_PORTFOLIO_SIZE + 1)
        )
        if candidate != route_id
    )[: _LEGACY_PORTFOLIO_SIZE - 1]
    old_rejections = source.get("rejections")
    old_rejections = old_rejections if isinstance(old_rejections, dict) else {}
    return {
        "route_id": route_id,
        "route_task_id": source.get("route_task_id"),
        "review_task_id": source.get("review_task_id"),
        "route_artifact": source.get("route_artifact"),
        "review_artifact": source.get("review_artifact"),
        "rationale": _one_line(source.get("rationale"))
        or "Selected by the prior authoritative portfolio selector.",
        "evidence_considered": _one_line(source.get("evidence_considered"))
        or "The completed prior portfolio and its independent reviews.",
        "resource_requirements": _one_line(source.get("resource_requirements"))
        or "Carry forward the resource requirements recorded by the selected route.",
        "unresolved_risks": source.get("unresolved_risks"),
        "rejections": {
            candidate: _one_line(old_rejections.get(candidate)) or _UNSELECTED
            for candidate in others
        },
        "migrated_from_legacy": True,
    }


def migrate_legacy_idea_selection(
    project_root: Path,
    *,
    state_root: Path | None = None,
    materialize_handoff: bool = True,
) -> bool:
    """Move an old completed selector into pipeline state without rerunning it."""
    project_root, state_root = _resolved_roots(project_root, state_root)
    handoff: dict[str, Any] | None = None
    migrated_selection = False
    with _state_lock(state_root):
        payload = read_pipeline_state(state_root)
        if not isinstance(payload, dict):
            return False
        if str(payload.get("vertical") or "").strip().lower() != "research":
            return False
        recorded = payload.get("selected_idea")
        handoff = _valid_selected_idea(recorded if isinstance(recorded, dict) else None)
        if payload.get("legacy_selection_consumed") is not True:
            payload["legacy_selection_consumed"] = True
            migrated = (
                None if handoff is not None
                else _valid_selected_idea(_legacy_selection(project_root))
            )
            if migrated is not None:
                payload["selected_idea"] = migrated
                payload["current_verdict"] = "idea_selected"
                payload["next_action"] = (
                    "Resume the mapped research stage with the selected idea; "
                    "mapped stages must be reviewed under the current four-stage "
                    "standards."
                )
                handoff = migrated
                migrated_selection = True
            write_pipeline_state(state_root, payload)
    if materialize_handoff and handoff is not None:
        _write_handoff(project_root, handoff)
    return migrated_selection


__all__ = ["migrate_legacy_idea_selection"]
