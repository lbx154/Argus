"""argus.manager._helpers — shared constants, config readers, and light utilities.

This module is the base dependency for all Manager sub-modules.  It contains
only things that are (a) used by more than one module and (b) carry no imports
from other manager sub-modules, so it sits at the root of the DAG and avoids
circular imports.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from ..core.run_gateway import run_exec as gateway_run_exec  # noqa: F401 — re-exported

log = logging.getLogger(__name__)

_DEFAULT_MANAGER_REASONING_EFFORT = "high"
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


def _manager_model() -> str:
    from ..core.knobs import resolve_role_model

    return resolve_role_model(
        "manager",
        role_env="ARGUS_SKILL_MANAGER_MODEL",
    )


