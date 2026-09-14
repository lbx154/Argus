"""Generic pipeline-state storage with one legacy-path compatibility reader."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

PIPELINE_STATE_RELATIVE = Path(".argus") / "PIPELINE_STATE.json"
LEGACY_PIPELINE_STATE_RELATIVE = Path("research") / "PIPELINE_STATE.json"


def primary_pipeline_state_path(project_root: object) -> Path:
    return Path(str(project_root)).expanduser() / PIPELINE_STATE_RELATIVE


def legacy_pipeline_state_path(project_root: object) -> Path:
    return Path(str(project_root)).expanduser() / LEGACY_PIPELINE_STATE_RELATIVE


def pipeline_state_path(project_root: object) -> Path:
    """Return the authoritative state path, falling back to the legacy file."""
    primary = primary_pipeline_state_path(project_root)
    if primary.exists():
        return primary
    legacy = legacy_pipeline_state_path(project_root)
    return legacy if legacy.exists() else primary


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
    """Atomically write the generic path; legacy files remain read-only inputs."""
    if not isinstance(payload, dict):
        raise TypeError("pipeline state payload must be a dict")
    path = primary_pipeline_state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident():x}.{uuid.uuid4().hex[:8]}"
    )
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


__all__ = [
    "LEGACY_PIPELINE_STATE_RELATIVE",
    "PIPELINE_STATE_RELATIVE",
    "legacy_pipeline_state_path",
    "pipeline_state_exists",
    "pipeline_state_path",
    "primary_pipeline_state_path",
    "read_pipeline_state",
    "write_pipeline_state",
]
