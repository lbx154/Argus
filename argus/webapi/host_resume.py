"""Resume work a host shutdown interrupted, when the server starts again.

A deploy or reboot restarts the runtime with SIGTERM to every process. A daemon
that receives one without an operator's stop request records "stopped by host
shutdown, resumable" (``daemon.state.record_host_shutdown``). Nothing else
would start it again: the server owns work (docs/daemon-lifecycle.md), so its
startup restarts those daemons, one at a time, within
``ARGUS_SKILL_MAX_ACTIVE_DAEMONS``.

Never resumed: an operator stop or pause (they clear the record), a campaign
the operator or Planner disabled since, a first task held for confirmation, a
project parked for another or behind an open runtime-failure circuit. Parked
tasks stay parked: unlike the operator's Start, this does not resume them.
While the workspace is paused nothing is resumed and every record is kept
until the pause is lifted.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..core.hosting import work_pause
from ..daemon.state import (
    HOST_SHUTDOWN_STOP_REASON,
    HostShutdown,
    clear_host_shutdown,
    read_continuous_state,
    read_daemon_status,
    read_host_shutdown,
)
from . import project_state
from .daemon_services import ProjectDaemonStarter

log = logging.getLogger(__name__)

_UNFINISHED_STATUSES = frozenset({"pending", "running", "in_progress", "claimed"})


def _resume_kind(life_dir: Path, record: HostShutdown) -> str | None:
    """``"campaign"``, ``"tasks"``, or None when nothing may be resumed."""
    from ..life.memory import LifeMemory
    from ..life.runtime_failure_circuit import active_runtime_failure_circuit
    from .launch_hold import read_launch_hold

    if read_launch_hold(life_dir) is not None:
        return None
    if (life_dir / "daemon.parked.json").exists():
        return None
    if active_runtime_failure_circuit(life_dir) is not None:
        return None
    if record.campaign_generation is not None:
        continuous = read_continuous_state(life_dir)
        # Enabled: killed before it could quiesce. Disabled for any other
        # reason: someone stopped the campaign since, and that stands.
        if continuous.objective.strip() and (
            continuous.enabled or continuous.done_reason == HOST_SHUTDOWN_STOP_REASON
        ):
            return "campaign"
    items = LifeMemory.open(life_dir).backlog.active()
    if any(item.status in _UNFINISHED_STATUSES for item in items):
        return "tasks"
    return None


def _max_active_daemons() -> int:
    from ..core.knobs import DEFAULT_MAX_ACTIVE_DAEMONS, resolve_knob

    try:
        return max(0, int(resolve_knob(
            "ARGUS_SKILL_MAX_ACTIVE_DAEMONS", str(DEFAULT_MAX_ACTIVE_DAEMONS),
        ).value))
    except (OSError, RuntimeError, TypeError, ValueError):
        return DEFAULT_MAX_ACTIVE_DAEMONS


def _alive_count(life_dirs: list[Path]) -> int:
    count = 0
    for life_dir in life_dirs:
        try:
            count += bool(read_daemon_status(life_dir).alive)
        except OSError:
            continue
    return count


def resume_host_stopped_daemons(
    roots: list[Path], start: ProjectDaemonStarter,
) -> list[str]:
    """Start each project a host shutdown interrupted; return the started ids."""
    started: list[str] = []
    limit = _max_active_daemons()
    for root in roots:
        if (pause := work_pause(root)) is not None:
            log.info("not resuming host-stopped daemons in %s: %s", root, pause["reason"])
            continue
        try:
            life_dirs = project_state.project_life_dirs(root)
        except OSError:
            log.exception("cannot scan host-stopped daemons root=%s", root)
            continue
        pending: list[tuple[Path, HostShutdown]] = []
        for life_dir in life_dirs:
            record = read_host_shutdown(life_dir)
            if record is not None:
                pending.append((life_dir, record))
        # The most recently interrupted work first.
        pending.sort(key=lambda row: row[1].stopped_at, reverse=True)
        for life_dir, record in pending:
            sid = life_dir.name
            try:
                if read_daemon_status(life_dir).alive:
                    clear_host_shutdown(life_dir)
                    continue
                kind = _resume_kind(life_dir, record)
                if kind is None:
                    log.info("host shutdown of %s left nothing to resume", sid)
                    clear_host_shutdown(life_dir)
                    continue
                if limit > 0 and _alive_count(life_dirs) >= limit:
                    # Keep the record: the operator's Start, or the next
                    # server start with room, resumes it.
                    log.info(
                        "not resuming %s after host shutdown: %s of %s daemons active",
                        sid, _alive_count(life_dirs), limit,
                    )
                    continue
                result = start(
                    sid, global_root=root, resume_continuous=kind == "campaign",
                )
            except (OSError, RuntimeError, ValueError):
                log.exception("cannot resume daemon after host shutdown sid=%s", sid)
                continue
            if result is not None and result.get("rc") == 0:
                clear_host_shutdown(life_dir)
                started.append(sid)
                log.info("resumed %s after host shutdown (%s)", sid, kind)
            else:
                log.warning(
                    "could not resume %s after host shutdown: %s",
                    sid, (result or {}).get("error") or (result or {}).get("rc"),
                )
    return started


def start_host_resume_thread(
    roots: list[Path], start: ProjectDaemonStarter,
) -> threading.Thread:
    """Resume in the background: spawning daemons must not delay serving."""
    thread = threading.Thread(
        target=resume_host_stopped_daemons,
        args=(roots, start),
        name="argus-host-resume",
        daemon=True,
    )
    thread.start()
    return thread
