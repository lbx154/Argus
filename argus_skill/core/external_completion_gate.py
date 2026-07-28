"""Optional project-local gate that must pass before project completion.

The gate is configured by ``ARGUS_SKILL_EXTERNAL_COMPLETION_GATE`` as
``relative/path.json:key.path``.  It is intentionally generic: controllers can
own the authoritative outcome while Argus owns the work needed to reach it.
Argus may read the aggregate gate, but must never manufacture or edit it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ENV_NAME = "ARGUS_SKILL_EXTERNAL_COMPLETION_GATE"
REWORK_STAGE_ENV_NAME = "ARGUS_SKILL_EXTERNAL_COMPLETION_REWORK_STAGE"


def external_completion_gate_issue(
    project_root: Path | str,
    *,
    spec: str | None = None,
) -> str:
    """Return an unmet-gate reason, or ``""`` when disabled/satisfied."""
    raw = (spec if spec is not None else os.environ.get(ENV_NAME, "")).strip()
    if not raw:
        return ""
    path_text, separator, key_text = raw.partition(":")
    relative = Path(path_text.strip())
    key_path = (key_text if separator else "satisfied").strip() or "satisfied"
    root = Path(project_root).resolve()
    if relative.is_absolute() or ".." in relative.parts:
        return f"external completion gate has unsafe path: {path_text!r}"
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        return f"external completion gate escapes project root: {path_text!r}"
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"external completion gate is missing: {relative.as_posix()}"
    except (OSError, json.JSONDecodeError) as exc:
        return (
            f"external completion gate is unreadable: {relative.as_posix()} "
            f"({type(exc).__name__})"
        )
    for key in key_path.split("."):
        if not isinstance(value, dict) or key not in value:
            return (
                f"external completion gate key is missing: "
                f"{relative.as_posix()}:{key_path}"
            )
        value = value[key]
    if value is True:
        return ""
    return (
        f"external completion gate is not satisfied: "
        f"{relative.as_posix()}:{key_path}={value!r}"
    )


def external_completion_rework_stage() -> str:
    """Return the configured stage to reopen while the gate is unmet."""
    return os.environ.get(REWORK_STAGE_ENV_NAME, "").strip().lower()


__all__ = [
    "ENV_NAME",
    "REWORK_STAGE_ENV_NAME",
    "external_completion_gate_issue",
    "external_completion_rework_stage",
]
