"""Runtime checkout identity for cross-process compatibility diagnostics."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import __version__

_PROCESS_STARTED_AT = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def source_root() -> Path:
    """Return the checkout or installed package root that loaded this process."""
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def source_revision() -> str | None:
    configured = os.environ.get("ARGUS_SKILL_BUILD_REVISION", "").strip()
    if configured:
        return configured
    root = source_root()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip() if result.returncode == 0 else ""
    return revision or None


def runtime_identity() -> dict[str, Any]:
    configured_root = os.environ.get("ARGUS_SKILL_SOURCE_ROOT", "").strip()
    loaded_root = source_root()
    return {
        "package_version": __version__,
        "source_root": str(loaded_root),
        "configured_source_root": configured_root or None,
        "source_root_matches_config": (
            None
            if not configured_root
            else Path(configured_root).expanduser().resolve() == loaded_root
        ),
        "revision": source_revision(),
        "pid": os.getpid(),
        "python_version": platform.python_version(),
        "executable": sys.executable,
        "started_at": _PROCESS_STARTED_AT,
    }


__all__ = ["runtime_identity", "source_revision", "source_root"]
