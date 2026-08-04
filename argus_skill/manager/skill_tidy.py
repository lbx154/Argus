"""Agent-owned Skill placement.

The former manager-side propagation pipeline parsed, classified, renamed, and
copied Skill documents between layers. That is a semantic decision and has been
removed. Agents receive all configured library paths and edit an explicit
semantic destination themselves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

_ZERO_SHARED = {
    "to_shared": 0,
    "to_vertical_shared": 0,
    "updated": 0,
    "cached": 0,
    "stayed": 0,
    "errors": 0,
}


def propagate_runtime_skills_to_shared(
    runtime_store: Any,
    *,
    shared_root: Path,
    ledger_path: Path,
    classify_batch: Callable[[list[dict[str, str]]], Any],
    on_event: Any = None,
) -> dict[str, int]:
    _ = (runtime_store, shared_root, ledger_path, classify_batch, on_event)
    return dict(_ZERO_SHARED)


def propagate_after_mission(
    project_root: Path | str,
    runner: Any,
    *,
    project_state_dir: Path | str | None,
    shared_root: Path | str,
    on_event: Any = None,
) -> dict[str, int]:
    _ = (project_root, runner, project_state_dir, shared_root, on_event)
    return dict(_ZERO_SHARED)


__all__ = ["propagate_after_mission", "propagate_runtime_skills_to_shared"]
