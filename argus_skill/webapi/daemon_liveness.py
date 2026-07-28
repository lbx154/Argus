"""Web-facing daemon liveness across host and PID namespaces.

Host PID-lock ownership remains the only authority for lifecycle controls.
Fresh, internally consistent heartbeat sidecars may additionally prove that a
daemon is visible/running inside an external PID namespace.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..daemon.health import HEALTH_FILENAME

DEFAULT_NAMESPACE_HEARTBEAT_MAX_AGE_SECONDS = 300.0
_VISIBLE_PHASES = frozenset({"starting", "active", "waiting", "idle"})


@dataclass(frozen=True)
class WebDaemonLiveness:
    alive: bool
    control_available: bool
    source: str
    pid: int | None
    heartbeat_age_seconds: float | None = None
    phase: str = "stopped"
    last_progress_at: float | None = None
    last_progress_event: str = ""
    seconds_since_progress: float | None = None


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not an object")
    return value


def _heartbeat_max_age() -> float:
    raw = os.environ.get("ARGUS_SKILL_WEB_NAMESPACE_HEARTBEAT_S", "").strip()
    if not raw:
        return DEFAULT_NAMESPACE_HEARTBEAT_MAX_AGE_SECONDS
    try:
        return max(5.0, float(raw))
    except ValueError:
        return DEFAULT_NAMESPACE_HEARTBEAT_MAX_AGE_SECONDS


def web_daemon_liveness(
    life_dir: Path,
    status: Any,
    *,
    now: float | None = None,
) -> WebDaemonLiveness:
    """Return visible liveness without granting false host control."""
    if bool(getattr(status, "alive", False)):
        return WebDaemonLiveness(
            alive=True,
            control_available=True,
            source="pid_lock",
            pid=getattr(status, "pid", None),
            phase=str(getattr(status, "health_state", "active") or "active"),
            last_progress_at=getattr(status, "last_progress_at", None),
            last_progress_event=str(getattr(status, "last_progress_event", "") or ""),
            seconds_since_progress=getattr(status, "seconds_since_progress", None),
        )

    try:
        namespace_pid = int((life_dir / "daemon.pid").read_text().strip())
        sidecar = _read_object(life_dir / "daemon.status.json")
        health = _read_object(life_dir / HEALTH_FILENAME)
        if int(sidecar.get("pid")) != namespace_pid or int(health.get("pid")) != namespace_pid:
            raise ValueError("namespace daemon sidecars disagree on pid")
        phase = str(health.get("phase") or "unknown").strip().lower()
        if phase not in _VISIBLE_PHASES:
            raise ValueError("heartbeat phase is not live")
        last_event_at = float(health.get("last_event_at"))
        last_progress_at = float(health.get("last_progress_at"))
        current = float(now if now is not None else time.time())
        heartbeat_age = max(0.0, current - last_event_at)
        if heartbeat_age > _heartbeat_max_age():
            raise ValueError("heartbeat is stale")
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return WebDaemonLiveness(False, False, "none", None)

    return WebDaemonLiveness(
        alive=True,
        control_available=False,
        source="namespace_heartbeat",
        pid=namespace_pid,
        heartbeat_age_seconds=heartbeat_age,
        phase=phase,
        last_progress_at=last_progress_at,
        last_progress_event=str(health.get("last_progress_event") or ""),
        seconds_since_progress=max(0.0, current - last_progress_at),
    )


__all__ = [
    "DEFAULT_NAMESPACE_HEARTBEAT_MAX_AGE_SECONDS",
    "WebDaemonLiveness",
    "web_daemon_liveness",
]
