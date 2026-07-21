"""argus.manager._helpers — shared constants, config readers, and light utilities.

This module is the base dependency for all Manager sub-modules.  It contains
only things that are (a) used by more than one module and (b) carry no imports
from other manager sub-modules, so it sits at the root of the DAG and avoids
circular imports.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..core.run_gateway import run_exec as gateway_run_exec  # noqa: F401 — re-exported
from ..core.runner_errors import result_has_missing_resume_target  # noqa: F401 — re-exported

# Verticals that run a lean optimize/speedrun loop rather than the paper pipeline.
_OPTIMIZE_VERTICALS = frozenset(
    {"speedrun", "kernel_engineering", "nanochat", "nanogpt_speedrun", "kernelbench"}
)

log = logging.getLogger(__name__)

_DEFAULT_MANAGER_REASONING_EFFORT = "xhigh"
_DEFAULT_FAST_ROUTE_MIN_CONFIDENCE = 0.75
_DEFAULT_FAST_ROUTE_MAX_TASK_CHARS = 12_000
_DEFAULT_FAST_ROUTE_MAX_PROMPT_CHARS = 24_000
_DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS = 32_000


def _manager_backend_failure(result: Any) -> tuple[bool, str]:
    """Return Manager failure status and its best diagnostic.

    stderr is retained diagnostic output, not an independent failure signal.
    Required Manager output is validated separately by each consumer.
    """
    fatal = str(getattr(result, "fatal_error", "") or "").strip()
    failed = bool(
        int(getattr(result, "exit_code", 0) or 0) != 0
        or getattr(result, "turn_failed", False)
        or fatal
    )
    if not failed or fatal:
        return failed, fatal
    stderr = "\n".join(
        map(str, getattr(result, "stderr_lines", None) or [])
    ).strip()
    return True, stderr


def _manager_reasoning_effort() -> str:
    for key in (
        "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
        "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    ):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw
    return _DEFAULT_MANAGER_REASONING_EFFORT


def _manager_vertical_reasoning_effort() -> str:
    return (
        os.environ.get("ARGUS_SKILL_MANAGER_VERTICAL_REASONING_EFFORT", "low").strip()
        or "low"
    )


def _manager_model() -> str:
    from ..core.knobs import resolve_role_model

    return resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
    )


def _manager_fast_route_enabled() -> bool:
    raw = os.environ.get("ARGUS_SKILL_MANAGER_FAST_ROUTE")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _manager_route_positive_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default


def _manager_fast_route_min_confidence() -> float:
    raw = (os.environ.get("ARGUS_SKILL_MANAGER_FAST_ROUTE_MIN_CONFIDENCE") or "").strip()
    try:
        return min(1.0, max(0.0, float(raw))) if raw else _DEFAULT_FAST_ROUTE_MIN_CONFIDENCE
    except ValueError:
        return _DEFAULT_FAST_ROUTE_MIN_CONFIDENCE


def _manager_safe_mode() -> bool:
    raw = os.environ.get("ARGUS_SKILL_SAFE_MODE")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None
