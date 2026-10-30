"""Life-mode 7×24 worker: detached background process that drains the
backlog forever.

This is the substrate behind ``argus --daemon`` and the non-interactive
executor behind the Ink/Web cockpit. Both build the same
:class:`~argus.life.supervisor.LifeSupervisor`
against the current project's split memory bundle, but the worker has
no TTY and exits only on SIGTERM /
SIGINT.

Coordination with the cockpit is provided by the backlog state machine
(:meth:`Backlog.claim_next` is atomic) plus the per-project ``daemon.pid`` lock.

The cockpit can submit and inspect while the daemon drains in the background.
Concurrent clients cannot
double-execute because :meth:`Backlog.claim_next` performs an atomic
CAS pending→running on the on-disk JSONL file.

This module is a thin facade: the actual lifecycle-phase implementations live
in sibling ``_life_worker_*`` modules (identity/vault preflight, runtime
context, boot phases, run phases, admission) so no single module here exceeds
the maintainability line-count target. Names the tests patch or import from
this module are re-exported here.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess  # noqa: F401 — re-exported: tests patch life_worker.subprocess.run
import sys  # noqa: F401 — re-exported: tests read life_worker.sys.executable
import threading
import time  # noqa: F401 — re-exported: tests patch life_worker.time.sleep
from typing import Any

from ..core import paths as core_paths  # noqa: F401 — compatibility monkeypatch seam
from ..core import process_stop
from ..core.daemon_lock import (
    DaemonAlreadyRunning,
    acquire_global_daemon_lock,  # noqa: F401 — re-exported, monkeypatch seam
)
from ..life.supervisor import (
    LifeSupervisor,  # noqa: F401 — monkeypatch seam, see tests/daemon/test_life_worker.py
)

# -- re-exports: daemon admission / workspace / spawn ------------------------
from ._life_worker_admission import (  # noqa: F401 — re-exported, see __all__
    _acquire_daemon_lock_with_timeout,
    _acquire_daemon_spawn_lock,
    _active_daemon_count,
    _active_workspace_owner,
    _launcher_failure_message,
    _max_active_daemons,
    _release_daemon_spawn_lock,
    _workspace_start_error,
    run_foreground,
    run_handoff_child,
    spawn_detached_daemon,
    spawn_detached_daemon_clean,
)

# -- lifecycle-phase mixins (boot phases, run phases) ------------------------
from ._life_worker_boot import (
    LifeWorkerBootMixin,
    _RunForeverState,  # noqa: F401 — re-exported, see __all__
)

# -- re-exports: manager-handoff identity ---------------------------------
from ._life_worker_identity import (  # noqa: F401 — re-exported, see __all__
    _apply_continuous_suppression,
    _daemon_objective_requires_stage_reset,
    _rearm_operator_drain_for_resume,
    _write_manager_handoff_identity,
)
from ._life_worker_run import LifeWorkerRunMixin

# -- re-exports: runner namespace / runtime context / supervisor config -----
from ._life_worker_runtime_context import (  # noqa: F401 — re-exported, see __all__
    _build_supervisor_config,
    _DaemonSink,
    _runner_namespace,
    _worker_runtime_context,
)
from .config import LifeWorkerConfig
from .config import config_payload as _config_payload
from .handoff import (
    _HANDOFF_CONFIG_ENV,
    _HANDOFF_READY_ENV,
    _HANDOFF_TOKEN_ENV,
    _spawn_handoff_candidate,
    _strip_git_config_injection,
)
from .state import (
    ContinuousConfigState,
    DaemonStatus,
    DaemonStopRequest,
    _daemon_log_path,
    _daemon_pid_path,
    _daemon_status_payload,
    _new_boot_id,
    _point_active_daemon_log,
    _process_alive,
    _redirect_std_to_log,
    continuous_mode_error,
    daemon_drain_requested,
    disable_continuous_config,
    format_spend_status,  # noqa: F401 — re-exported, see __all__
    read_continuous_config,
    read_continuous_state,
    read_daemon_control_stop,
    read_daemon_status,
    record_host_shutdown,
    stop_daemon,
    write_continuous_config,
)

log = logging.getLogger(__name__)


__all__ = [
    "LifeWorkerConfig",
    "LifeWorker",
    "DaemonStatus",
    "ContinuousConfigState",
    "continuous_mode_error",
    "disable_continuous_config",
    "format_spend_status",
    "read_daemon_status",
    "stop_daemon",
    "spawn_detached_daemon",
    "spawn_detached_daemon_clean",
    "run_handoff_child",
    "read_continuous_state",
    "read_continuous_config",
    "write_continuous_config",
    "_process_alive",
    "_redirect_std_to_log",
    "_daemon_log_path",
    "_daemon_pid_path",
    "_daemon_status_payload",
    "_new_boot_id",
    "_point_active_daemon_log",
    "_config_payload",
    "_HANDOFF_CONFIG_ENV",
    "_HANDOFF_READY_ENV",
    "_HANDOFF_TOKEN_ENV",
    "_acquire_daemon_lock_with_timeout",
    "_spawn_handoff_candidate",
    "_strip_git_config_injection",
    "DaemonAlreadyRunning",
]


class LifeWorker(LifeWorkerBootMixin, LifeWorkerRunMixin):
    """The 7×24 background worker.

    Construct, then call :meth:`run_forever` from the daemon process.
    Stops cleanly on SIGTERM / SIGINT — the supervisor's tick is one
    mission so there is at most one outstanding ``running`` item when
    the signal lands; the next process startup will reap it via
    :meth:`Backlog.reap_orphans` and mark it ``failed``.
    """

    def __init__(self, config: LifeWorkerConfig) -> None:
        self.config = config
        self._stop = threading.Event()
        self._mission_stop = threading.Event()
        self._operator_stop_requested = False
        # A SIGTERM that no operator stop requested (see _record_host_shutdown).
        self._host_shutdown = False
        self._adopted_continuous_generation: int | None = None
        self._started_at: float | None = None
        self._missions_completed = 0
        self._control_thread: threading.Thread | None = None
        self._control_started_at_iso = ""
        self._running_stall_stop = threading.Event()
        self._running_stall_thread: threading.Thread | None = None
        self._supervisor_execution_active = threading.Event()
        self._supervisor_execution_threads: dict[str, threading.Thread] = {}
        # Blue/green successor waiting for this process's daemon lock, if any.
        self._handoff_successor_pid = 0

    # -- signal handling ------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:  # noqa: ANN401
            log.info("daemon: received signal %s, requesting stop", signum)
            drain = daemon_drain_requested(
                self.config.life_dir,
                pid=os.getpid(),
            )
            if not drain and signum == signal.SIGTERM:
                self._record_host_shutdown(signum)
            self.request_process_stop(drain=drain)

        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        # Belt-and-suspenders: ``spawn_detached_daemon`` already calls
        # ``setsid`` so SIGHUP from a closing controlling-TTY cannot
        # reach us, but we explicitly ignore SIGHUP anyway so an
        # external operator (or an over-eager process supervisor)
        # cannot accidentally bring the 7×24 worker down by sending
        # one. Operators stop the daemon with SIGTERM / ``--daemon-stop``.
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        except (AttributeError, ValueError, OSError):
            # SIGHUP is POSIX-only; on Windows ``signal.SIGHUP`` is
            # missing. Ignoring is a no-op on Windows anyway.
            pass
        self._start_control_watcher()

    def _record_host_shutdown(self, signum: int) -> None:
        """Mark a SIGTERM no operator asked for as a resumable host shutdown.

        Every Argus stop and pause (``stop_daemon``, ``request_daemon_stop``)
        writes this boot's PID-bound stop request before it signals, so a
        SIGTERM without one came from the host: a service manager restarting
        the runtime, a deploy, a reboot. The work it interrupted is resumed
        when the web server starts again (``webapi.host_resume``). A boot with
        no published identity cannot tell the two apart and records nothing,
        as before.
        """
        try:
            started_at_iso = self._control_started_at_iso
            if not started_at_iso:
                status = read_daemon_status(self.config.life_dir)
                if not status.alive or status.pid != os.getpid():
                    return
                started_at_iso = str(status.started_at_iso or "")
            if not started_at_iso:
                return
            if read_daemon_control_stop(
                self.config.life_dir,
                pid=os.getpid(),
                started_at_iso=started_at_iso,
            ) is not None:
                return
            record_host_shutdown(
                self.config.life_dir,
                pid=os.getpid(),
                started_at_iso=started_at_iso,
                signum=signum,
                campaign_generation=self._adopted_continuous_generation,
            )
            self._host_shutdown = True
            log.info("daemon: stopped by host shutdown, resumable")
        except (OSError, TypeError, ValueError):  # never block the stop itself
            log.exception("daemon: failed to record host shutdown")

    def request_process_stop(self, *, drain: bool = False) -> None:
        """Set the worker's cooperative stop events.

        This is shared by POSIX signals and the PID-bound file control channel
        used on Windows, where ``os.kill(pid, SIGTERM)`` is a hard process
        termination and cannot invoke Python's signal handler.
        """
        self._operator_stop_requested = True
        process_stop.request_stop()
        self._stop.set()
        if not drain:
            self._mission_stop.set()

    def _start_control_watcher(self) -> None:
        """Watch this exact daemon boot's out-of-band stop request."""
        if self._control_thread is not None:
            return
        status = read_daemon_status(self.config.life_dir)
        if (
            not status.alive
            or status.pid != os.getpid()
            or not status.started_at_iso
        ):
            # Unit-created workers and pre-publication failures have no process
            # identity to bind safely, so they must not consume control files.
            return
        started_at_iso = status.started_at_iso
        self._control_started_at_iso = started_at_iso

        def _watch() -> None:
            last_request: DaemonStopRequest | None = None
            # A drain request sets ``_stop`` but deliberately leaves the current
            # mission running. Keep watching so a later operator click can
            # escalate that graceful drain to an immediate, PID-bound interrupt.
            while not self._mission_stop.is_set():
                request = read_daemon_control_stop(
                    self.config.life_dir,
                    pid=os.getpid(),
                    started_at_iso=started_at_iso,
                )
                # A drain and its escalation can share one Windows clock tick.
                if request is not None and request != last_request:
                    last_request = request
                    log.info(
                        "daemon: received PID-bound %s request",
                        "drain" if request.drain else "stop",
                    )
                    self.request_process_stop(drain=request.drain)
                    if not request.drain:
                        return
                time.sleep(0.1)

        self._control_thread = threading.Thread(
            target=_watch,
            name="argus-daemon-control",
            daemon=True,
        )
        self._control_thread.start()
