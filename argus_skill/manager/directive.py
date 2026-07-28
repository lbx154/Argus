"""Durable Manager steering shared by Planner and Engineer processes."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

ACTIVE_MANAGER_DIRECTIVE_FILENAME = "active_manager_directive.json"
ACTIVE_MANAGER_DIRECTIVE_PREFIX = (
    "[ACTIVE MANAGER STEERING DIRECTIVE - persists until replaced or cleared] "
)
_DIRECTIVE_VERSION = 1


@dataclass(frozen=True)
class ActiveManagerDirective:
    text: str
    source: str
    objective_sha256: str
    revision: str
    set_at: float
    version: int = _DIRECTIVE_VERSION


def _directive_path(state_root: Path | str) -> Path:
    return Path(state_root) / ACTIVE_MANAGER_DIRECTIVE_FILENAME


def _current_objective_sha256(state_root: Path | str) -> str:
    try:
        payload = json.loads(
            (Path(state_root) / "continuous.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    objective = str(payload.get("objective") or "").strip()
    if not objective:
        return ""
    return hashlib.sha256(objective.encode("utf-8")).hexdigest()


def set_active_manager_directive(
    state_root: Path | str,
    text: str,
    *,
    source: str = "manager.steer",
) -> ActiveManagerDirective:
    """Replace the active directive atomically."""
    normalized = str(text or "").strip()
    if not normalized:
        raise ValueError("manager directive must not be empty")
    record = ActiveManagerDirective(
        text=normalized,
        source=str(source or "").strip() or "manager",
        objective_sha256=_current_objective_sha256(state_root),
        revision=uuid.uuid4().hex,
        set_at=time.time(),
    )
    path = _directive_path(state_root)
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    asdict(record),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return record


def load_active_manager_directive(
    state_root: Path | str | None,
) -> ActiveManagerDirective | None:
    """Load the current-objective directive without consuming it."""
    if not state_root:
        return None
    try:
        payload = json.loads(
            _directive_path(state_root).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    text = str(payload.get("text") or "").strip()
    if not text:
        return None
    recorded_objective = str(payload.get("objective_sha256") or "").strip()
    current_objective = _current_objective_sha256(state_root)
    if (
        recorded_objective
        and current_objective
        and recorded_objective != current_objective
    ):
        return None
    try:
        set_at = float(payload.get("set_at") or 0.0)
        version = int(payload.get("version") or 0)
    except (TypeError, ValueError):
        return None
    if version != _DIRECTIVE_VERSION:
        return None
    return ActiveManagerDirective(
        text=text,
        source=str(payload.get("source") or "").strip() or "manager",
        objective_sha256=recorded_objective,
        revision=str(payload.get("revision") or "").strip(),
        set_at=set_at,
        version=version,
    )


def active_manager_directive_message(
    state_root: Path | str | None,
) -> str:
    record = load_active_manager_directive(state_root)
    if record is None:
        return ""
    return ACTIVE_MANAGER_DIRECTIVE_PREFIX + record.text


def clear_active_manager_directive(state_root: Path | str) -> bool:
    """Clear the directive explicitly; return whether one existed."""
    path = _directive_path(state_root)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


__all__ = [
    "ACTIVE_MANAGER_DIRECTIVE_FILENAME",
    "ACTIVE_MANAGER_DIRECTIVE_PREFIX",
    "ActiveManagerDirective",
    "active_manager_directive_message",
    "clear_active_manager_directive",
    "load_active_manager_directive",
    "set_active_manager_directive",
]
