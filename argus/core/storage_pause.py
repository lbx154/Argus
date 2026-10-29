"""Work paused because the workspace's storage is full.

A hosted runtime's storage monitor (``trial_server/deploy/native_runtime.py``)
records this in the Argus home when the workspace stays over its limit after
the shared download caches were pruned, and removes it once usage is back
under. While it is there no project's daemon starts and work a host shutdown
interrupted waits; the project page says why. Every file stays on disk.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .atomic_replace import write_atomic

STORAGE_PAUSE_FILE = "storage-pause.json"


def storage_pause_path(root: str | Path) -> Path:
    return Path(root) / STORAGE_PAUSE_FILE


def record_storage_pause(root: str | Path, *, used_bytes: int, limit_bytes: int) -> None:
    Path(root).mkdir(parents=True, exist_ok=True)
    write_atomic(storage_pause_path(root), json.dumps({
        "schema_version": 1, "used_bytes": int(used_bytes), "limit_bytes": int(limit_bytes),
        "since": time.time(),
    }))


def clear_storage_pause(root: str | Path) -> None:
    storage_pause_path(root).unlink(missing_ok=True)


def read_storage_pause(root: str | Path) -> dict[str, Any] | None:
    """``{"used_bytes", "limit_bytes", "since"}`` while work is paused, else None."""
    try:
        data = json.loads(storage_pause_path(root).read_text(encoding="utf-8"))
        return {
            "used_bytes": int(data["used_bytes"]), "limit_bytes": int(data["limit_bytes"]),
            "since": float(data.get("since") or 0),
        }
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Unreadable but present: still paused, size unknown.
        return {"used_bytes": 0, "limit_bytes": 0, "since": 0.0}


def storage_pause_error(pause: dict[str, Any]) -> str:
    """Why a daemon did not start; the web page words it from the numbers."""
    def gb(value: int) -> str:
        return f"{value / 1024**3:.1f}"

    return (
        f"workspace storage is full ({gb(pause['used_bytes'])} GB of {gb(pause['limit_bytes'])} GB); "
        "work stays paused until files are removed"
    )
