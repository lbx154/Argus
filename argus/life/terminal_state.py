"""Comparable facts for "could the Planner act on anything new?".

The terminal guard is deliberately narrower than the whole workspace.
Planner/Wiki/runtime logs are process data, not new operator intent; letting
them in would let the agent's own bookkeeping wake the Planner again. Nothing
is read beyond ``stat``: the facts are compared as plain JSON text.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from ..core.file_facts import file_facts
from ..core.workspace_facts import IGNORED_NAMES, fact_text, workspace_facts

_VOLATILE_KEYS = frozenset({
    "created_at",
    "event_sequence",
    "last_event_ts",
    "rendered_at",
    "rendering_timestamp",
    "sequence",
    "ts",
    "updated_at",
})

# Rewritten by the runtime itself, never new project semantics.
_PROCESS_PATHS = (
    "mission-view.json",
    "mission-view.lock",
    "research/GROUND_TRUTH.md",
    "research/raw",
)


def semantic_terminal_value(value: Any, *, field_name: str = "") -> Any:
    """Drop refresh-only metadata while preserving semantic JSON state."""
    if isinstance(value, dict):
        return {
            str(key): semantic_terminal_value(item, field_name=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        items = [
            semantic_terminal_value(item, field_name=field_name)
            for item in value
        ]
        if "summary" in field_name.lower():
            return sorted(
                items,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        return items
    return value


def _project_ignore(project_root: Path, state_root: Path | None) -> tuple[str, ...]:
    """The process paths plus the state root, when it lives inside the project."""
    if state_root is not None:
        try:
            prefix = state_root.resolve().relative_to(project_root.resolve()).as_posix()
        except (OSError, ValueError):
            prefix = ""
        if prefix not in ("", "."):
            return (*_PROCESS_PATHS, prefix)
    return _PROCESS_PATHS


def _review_files(
    artifact_root: Path,
    *,
    state_root: Path | None = None,
) -> list[Path]:
    """Find canonical review files without descending into environments/logs."""
    try:
        from ..skills.vertical_select import resolve_vertical

        if resolve_vertical(state_root or artifact_root) == "research":
            current = artifact_root / "paper" / "REVIEW.md"
            return [current] if current.is_file() else []
    except Exception:  # noqa: BLE001 - generic discovery remains the fallback
        pass
    found: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(artifact_root):
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name not in IGNORED_NAMES and not name.endswith(".egg-info")
            ]
            if "REVIEW.md" in filenames:
                found.append(Path(dirpath) / "REVIEW.md")
    except OSError:
        return []
    return sorted(found)


def build_terminal_idle_signature(
    *,
    objective: str,
    stage: str,
    backlog: Iterable[tuple[str, str, str]],
    artifact_root: Path,
    project_root: Path,
    state_root: Path | None,
    completion_contract: dict[str, Any] | None,
) -> str:
    """The state that could justify a new Planner call, as comparable text."""
    from ..core.pipeline_state import read_pipeline_state

    try:
        pipeline = semantic_terminal_value(read_pipeline_state(state_root or artifact_root))
    except (OSError, ValueError):
        pipeline = "unavailable"
    reviews = []
    for path in _review_files(project_root, state_root=state_root or artifact_root):
        try:
            relative = path.relative_to(project_root).as_posix()
        except ValueError:
            relative = str(path)
        reviews.append([relative, file_facts(path) if path.is_file() else []])
    return fact_text({
        "objective": str(objective or ""),
        "stage": str(stage or ""),
        "backlog": sorted(backlog),
        "pipeline": pipeline,
        "reviews": reviews,
        "project": workspace_facts(
            project_root, ignore=_project_ignore(project_root, state_root),
        ),
        "contract": semantic_terminal_value(completion_contract or {}),
    })


def build_project_state_signature(
    *,
    project_root: Path,
    state_root: Path | None = None,
) -> str:
    """The project's semantic files as comparable text, without runtime bookkeeping."""
    return fact_text(workspace_facts(
        project_root, ignore=_project_ignore(project_root, state_root),
    ))


__all__ = [
    "build_project_state_signature",
    "build_terminal_idle_signature",
    "semantic_terminal_value",
]
