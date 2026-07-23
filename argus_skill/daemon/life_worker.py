"""Life-mode 7×24 worker: detached background process that drains the
backlog forever.

This is the substrate behind ``argus-skill --daemon`` and the non-interactive
executor behind the Ink/Web cockpit. Both build the same
:class:`~argus_skill.life.supervisor.LifeSupervisor`
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
the maintainability line-count target. Every name previously importable from
this module (public or private) remains importable from here via explicit
re-export.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess  # noqa: F401 — re-exported: tests patch life_worker.subprocess.run
import sys  # noqa: F401 — re-exported: tests read life_worker.sys.executable
import threading
import time  # noqa: F401 — re-exported: tests patch life_worker.time.sleep
from pathlib import Path
from typing import Any

from ..core import paths as core_paths  # noqa: F401 — compatibility monkeypatch seam
from ..core.daemon_lock import (
    DaemonAlreadyRunning,
    acquire_global_daemon_lock,  # noqa: F401 — re-exported, monkeypatch seam
)
from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..life.supervisor import (
    LifeSupervisor,  # noqa: F401 — monkeypatch seam, see tests/daemon/test_life_worker.py
    global_daily_spend,
)

# -- re-exports: daemon admission / workspace / spawn ------------------------
from ._life_worker_admission import (  # noqa: F401 — re-exported, see __all__
    _acquire_daemon_lock_with_timeout,
    _acquire_daemon_spawn_lock,
    _acquire_daemon_workspace_lease,
    _active_daemon_count,
    _active_workspace_owner,
    _daemon_global_root,
    _max_active_daemons,
    _release_daemon_spawn_lock,
    _release_daemon_workspace_lease,
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

# -- re-exports: manager-handoff identity + vault/backend preflight ---------
from ._life_worker_identity import (  # noqa: F401 — re-exported, see __all__
    _MANAGER_HANDOFF_IDENTITY_FILE,
    _apply_continuous_suppression,
    _daemon_objective_requires_stage_reset,
    _effective_runner_backend,
    _legacy_manager_handoff_identity,
    _manager_handoff_identity_matches,
    _manager_handoff_identity_path,
    _objective_sha256,
    _preflight_route_on_codex,
    _read_manager_handoff_identity,
    _rearm_operator_drain_for_resume,
    _resume_matches_manager_handoff,
    _worker_vault_preflight_routes,
    _write_manager_handoff_identity,
    required_codex_routes,
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
from .config import config_from_payload as _config_from_payload
from .config import config_payload as _config_payload
from .handoff import (
    _HANDOFF_CONFIG_ENV,
    _HANDOFF_LOG_ENV,
    _HANDOFF_READY_ENV,
    _HANDOFF_TOKEN_ENV,
    _spawn_handoff_candidate,
    _strip_git_config_injection,
    _truthy_env,
)
from .state import (
    ContinuousConfigState,
    DaemonStatus,
    _daemon_log_path,
    _daemon_pid_path,
    _daemon_status_path,
    _daemon_status_payload,
    _new_boot_id,
    _point_active_daemon_log,
    _process_alive,
    _redirect_std_to_log,
    continuous_mode_error,
    daemon_drain_requested,
    disable_continuous_config,
    read_continuous_config,
    read_continuous_state,
    read_daemon_status,
    resolve_effective_budget,
    stop_daemon,
    wait_for_daemon_status,
    write_continuous_config,
)
from .state import (
    format_budget_status as _format_budget_status,
)

log = logging.getLogger(__name__)


def format_budget_status(journal: Any, *, status: Any | None = None) -> str:
    """Compatibility wrapper preserving the historical monkeypatch seam."""
    return _format_budget_status(
        journal,
        status=status,
        global_spend_fn=global_daily_spend,
    )

__all__ = [
    "LifeWorkerConfig",
    "LifeWorker",
    "DaemonStatus",
    "ContinuousConfigState",
    "continuous_mode_error",
    "disable_continuous_config",
    "format_budget_status",
    "resolve_effective_budget",
    "read_daemon_status",
    "stop_daemon",
    "wait_for_daemon_status",
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
    "_daemon_status_path",
    "_daemon_status_payload",
    "_new_boot_id",
    "_point_active_daemon_log",
    "_config_from_payload",
    "_config_payload",
    "_HANDOFF_CONFIG_ENV",
    "_HANDOFF_LOG_ENV",
    "_HANDOFF_READY_ENV",
    "_HANDOFF_TOKEN_ENV",
    "_acquire_daemon_lock_with_timeout",
    "_spawn_handoff_candidate",
    "_strip_git_config_injection",
    "_truthy_env",
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
        # The host-global daily cap is the only monetary budget.
        budget_global_root = (
            Path(config.global_root).expanduser()
            if config.global_root is not None
            else (
                config.life_dir.parent.parent
                if config.life_dir.parent.name == "projects"
                else config.life_dir
            )
        )
        from ..core.knobs import resolve_budget_caps

        caps = resolve_budget_caps(
            project_state_dir=config.life_dir,
            global_root=budget_global_root,
        )
        config.global_daily_cap_usd = caps.global_daily_cap_usd
        self.config = config
        self._stop = threading.Event()
        self._mission_stop = threading.Event()
        self._operator_stop_requested = False
        self._adopted_continuous_generation: int | None = None
        self._started_at: float | None = None
        self._missions_completed = 0
        self._curator: Any = None  # resident teammate-pool Curator (built in run_forever)

    # -- signal handling ------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:  # noqa: ANN401
            log.info("daemon: received signal %s, requesting stop", signum)
            self._operator_stop_requested = True
            self._stop.set()
            if not daemon_drain_requested(
                self.config.life_dir,
                pid=os.getpid(),
            ):
                self._mission_stop.set()

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

    # -- main loop ------------------------------------------------------

    def _build_curator(self, runner: Any = None) -> Any:
        """Construct the resident Curator that owns every team campaign's pool,
        or ``None`` when this daemon has no project workspace (no teams without
        one). Lazily imported to keep the daemon free of team deps until needed.
        """
        workdir = self.config.project_workdir
        if workdir is None:
            return None
        from ..team.curator import Curator
        return Curator(
            project_root=Path(workdir),
            default_width=int(os.environ.get("ARGUS_TEAM_DEFAULT_WIDTH", "8")),
            tick_s=float(os.environ.get("ARGUS_TEAM_CURATOR_TICK_S", "5")),
            teammate_timeout_s=float(os.environ.get("ARGUS_TEAMMATE_TIMEOUT_S", "5400")),
            hard_grace_s=float(os.environ.get("ARGUS_TEAMMATE_HARD_GRACE_S", "600")),
            distill_fn=self._curator_distill_fn(runner),
            distill_interval_s=float(os.environ.get("ARGUS_SKILL_CURATOR_DISTILL_INTERVAL_S", "1260")),
            completion_fn=self._team_completion_summary_fn(runner),
            conversation_root=self.config.life_dir,
        )

    def _curator_distill_fn(self, runner: Any) -> Any:
        """Wrap the curator agent backend into a ``(prompt) -> str`` distill
        function, or ``None`` when no runner/backend is available (the Curator
        then runs deterministic-only — fold the board, write no strategy)."""
        backend = getattr(runner, "curator_backend", None) or getattr(runner, "backend", None)
        if backend is None:
            return None
        from ..core.knobs import resolve_role_model

        model = resolve_role_model("curator", role_env="ARGUS_SKILL_CURATOR_MODEL")
        effort = os.environ.get("ARGUS_SKILL_CURATOR_REASONING_EFFORT", "high")
        workdir = str(self.config.project_workdir) if self.config.project_workdir else None

        def _distill(prompt: str) -> str:
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(model=model or None, reasoning_effort=effort,
                                      skip_git_repo_check=True, full_auto=True,
                                      working_dir=workdir),
                run_label="curator.distill",
            )
            return getattr(result, "last_agent_message", "") or ""

        return _distill

    def _team_completion_summary_fn(self, runner: Any) -> Any:
        """Use the Manager backend for one concise Team completion chat summary."""
        backend = getattr(runner, "manager_backend", None) or getattr(runner, "backend", None)
        if backend is None:
            return None
        from ..core.knobs import resolve_role_model

        model = resolve_role_model("manager", role_env="ARGUS_SKILL_MODEL")
        workdir = str(self.config.project_workdir) if self.config.project_workdir else None

        def _summarize(prompt: str) -> str:
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=model or None,
                    reasoning_effort="low",
                    skip_git_repo_check=True,
                    full_auto=True,
                    working_dir=workdir,
                ),
                run_label="manager.team_summary",
            )
            return getattr(result, "last_agent_message", "") or ""

        return _summarize
