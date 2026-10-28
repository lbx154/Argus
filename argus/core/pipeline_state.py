"""Generic pipeline-state storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .atomic_replace import write_atomic

PIPELINE_STATE_RELATIVE = Path(".argus") / "PIPELINE_STATE.json"


def primary_pipeline_state_path(project_root: object) -> Path:
    return Path(str(project_root)).expanduser() / PIPELINE_STATE_RELATIVE


def pipeline_state_path(project_root: object) -> Path:
    """Return the authoritative state path."""
    return primary_pipeline_state_path(project_root)


def pipeline_state_exists(project_root: object) -> bool:
    return pipeline_state_path(project_root).is_file()


def read_pipeline_state(project_root: object) -> dict[str, Any]:
    try:
        payload = json.loads(
            pipeline_state_path(project_root).read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("pipeline state must contain a JSON object")
    return payload


def write_pipeline_state(project_root: object, payload: dict[str, Any]) -> Path:
    """Atomically write the state file."""
    if not isinstance(payload, dict):
        raise TypeError("pipeline state payload must be a dict")
    path = primary_pipeline_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path


__all__ = [
    "PIPELINE_STATE_RELATIVE",
    "pipeline_state_exists",
    "pipeline_state_path",
    "primary_pipeline_state_path",
    "read_pipeline_state",
    "write_pipeline_state",
]
