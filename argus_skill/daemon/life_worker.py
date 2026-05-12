"""Life-mode 7×24 worker: detached background process that drains the
backlog forever.

This is the substrate behind ``argus-skill --daemon``. It is the
non-interactive twin of :func:`argus_skill.apps._life_repl.run_life_chat_loop`:
both build the same :class:`~argus_skill.life.supervisor.LifeSupervisor`
against the same :class:`~argus_skill.life.memory.LifeMemory` root, but
the worker has no TTY, no slash commands, and no exit on Ctrl-D — only
on SIGTERM / SIGINT.

Coordination with the REPL is provided by the backlog state machine
(:meth:`Backlog.claim_next` is atomic) plus two distinct PID locks:

* ``<life_dir>/repl.pid``    — REPL singleton (per life-dir)
* ``<life_dir>/daemon.pid``  — daemon singleton (per life-dir)

The two can run side by side: a REPL session lets you /add and inspect
journal/backlog while the daemon drains in the background. They cannot
double-execute because :meth:`Backlog.claim_next` performs an atomic
CAS pending→running on the on-disk JSONL file.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.daemon_lock import DaemonAlreadyRunning, acquire_global_daemon_lock
from ..life.memory import LifeMemory, default_life_dir
from ..life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig

log = logging.getLogger(__name__)

__all__ = [
    "LifeWorkerConfig",
    "LifeWorker",
    "DaemonStatus",
    "ContinuousConfigState",
    "continuous_mode_error",
    "read_daemon_status",
    "stop_daemon",
    "spawn_detached_daemon",
    "read_continuous_state",
    "read_continuous_config",
    "write_continuous_config",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LifeWorkerConfig:
    """How the worker drains the backlog.

    All durations are seconds. ``poll_interval`` is how long the worker
    sleeps between :meth:`LifeSupervisor.tick` calls when the backlog
    is empty — when work is pending it does not sleep, it just keeps
    ticking.
    """

    life_dir: Path
    backend: str = "codex"  # "codex" | "memory"
    engineer_model: str = "gpt-5.4-mini"
    reviewer_model: str = "gpt-5.4"
    scientist_model: str = "gpt-5.4"
    per_mission_cap_usd: float = 30.0
    daily_cap_usd: float = 180.0
    poll_interval: float = 5.0
    log_path: Path | None = None  # defaults to <life_dir>/daemon.log
    continuous: bool = False
    continuous_objective: str = ""


# ---------------------------------------------------------------------------
# Disk-based continuous config (hot-reloadable by both daemon + REPL)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuousConfigState:
    enabled: bool = False
    objective: str = ""
    done_reason: str = ""
    done_at: str = ""


def continuous_mode_error(backend: str, enabled: bool, objective: str) -> str:
    """Return the public error string for an invalid continuous-mode request."""
    backend = backend.strip().lower()
    objective = objective.strip()
    if objective and not enabled:
        return "--objective requires --continuous"
    if enabled and not objective:
        return "--continuous requires a non-empty --objective"
    if enabled and backend == "memory":
        return (
            "--continuous requires a planning-capable life backend; "
            "ARGUS_SKILL_LIFE_BACKEND=memory cannot plan"
        )
    return ""

def _continuous_config_path(life_dir: Path) -> Path:
    return life_dir / "continuous.json"


def read_continuous_state(life_dir: Path) -> ContinuousConfigState:
    """Read the full ``continuous.json`` state blob."""
    path = _continuous_config_path(life_dir)
    if not path.exists():
        return ContinuousConfigState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ContinuousConfigState()
        def _text(value: Any) -> str:
            return "" if value is None else str(value)
        return ContinuousConfigState(
            enabled=bool(data.get("enabled", False)),
            objective=_text(data.get("objective", "")),
            done_reason=_text(data.get("done_reason", "")),
            done_at=_text(data.get("done_at", "")),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ContinuousConfigState()


def read_continuous_config(life_dir: Path) -> tuple[bool, str]:
    """Read ``(enabled, objective)`` from ``<life_dir>/continuous.json``.

    Returns ``(False, "")`` if the file is missing or malformed.
    """
    state = read_continuous_state(life_dir)
    return state.enabled, state.objective


def write_continuous_config(
    life_dir: Path,
    *,
    enabled: bool,
    objective: str,
    done_reason: str = "",
) -> None:
    """Atomically write ``continuous.json`` so the daemon can hot-reload.

    Uses write-to-temp + ``os.replace`` for atomicity.
    """
    objective = objective.strip()
    if enabled and not objective:
        log.warning("refusing to write invalid continuous config to %s", life_dir)
        return
    life_dir.mkdir(parents=True, exist_ok=True)
    path = _continuous_config_path(life_dir)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    data = {
        "enabled": enabled,
        "objective": objective,
    }
    if done_reason:
        data["done_reason"] = done_reason
        data["done_at"] = datetime.now(timezone.utc).isoformat()
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(str(tmp), str(path))
    except OSError:
        log.warning("failed to write continuous config to %s", path)

# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class LifeWorker:
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
        self._started_at: float | None = None
        self._missions_completed = 0

    # -- signal handling ------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:  # noqa: ANN401
            log.info("daemon: received signal %s, requesting stop", signum)
            self._stop.set()

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

    def run_forever(self) -> int:
        self._install_signal_handlers()
        self._started_at = time.time()

        cfg = self.config
        mem = LifeMemory.open(cfg.life_dir)
        mem.init()

        # Build the runner the same way the REPL does. Importing here
        # keeps daemon.life_worker free of CLI-only deps until needed.
        from ..apps._life_repl import LifeStderrSink, build_life_runner
        ns = _runner_namespace(cfg)
        runner = build_life_runner(ns)

        # Continuous drain: each LifeSupervisor.run() drains until the
        # backlog goes empty or the budget caps. Then we sleep
        # poll_interval seconds and try again — items may have been
        # /add'd from a coexisting REPL.
        from ..apps._life_repl import _inbox_drainer_for
        from ..life.event_log import JsonlEventSink

        # Telegram live-streaming reporter (daemon thread)
        stream_reporter = None
        try:
            from ..life.notify import TelegramStreamReporter
            stream_reporter = TelegramStreamReporter(stop_event=self._stop)
            stream_reporter.start()
            log.info("telegram stream reporter started")
        except Exception:  # noqa: BLE001
            log.debug("telegram stream reporter unavailable; continuing")

        sink = JsonlEventSink(
            _DaemonSink(self, stream_reporter=stream_reporter),
            life_dir=cfg.life_dir,
        )

        # Build a config provider that reads continuous.json from disk,
        # so the REPL can enable/disable continuous mode while the daemon
        # is running — no daemon restart needed.
        def _continuous_provider() -> tuple[bool, str]:
            enabled, objective = read_continuous_config(cfg.life_dir)
            if continuous_mode_error(cfg.backend, enabled, objective):
                if enabled:
                    write_continuous_config(
                        cfg.life_dir,
                        enabled=False,
                        objective=objective,
                    )
                return False, objective
            return enabled, objective

        # Seed continuous config from disk (or CLI flags).
        init_continuous, init_objective = _continuous_provider()
        if cfg.continuous:
            # CLI flags override disk — also persist them so REPL sees.
            init_continuous = True
            init_objective = cfg.continuous_objective or init_objective
            write_continuous_config(
                cfg.life_dir,
                enabled=True,
                objective=init_objective,
            )

        sup_cfg = LifeSupervisorConfig(
            budget=LifeBudget(
                per_mission_cap_usd=cfg.per_mission_cap_usd,
                daily_cap_usd=cfg.daily_cap_usd,
                # Soft cap per drain pass; we re-loop forever anyway.
                max_missions=64,
            ),
            poll_interval_seconds=2.0,
            stop_event=self._stop,
            user_inbox=_inbox_drainer_for(cfg.life_dir),
            continuous=init_continuous,
            continuous_objective=init_objective,
            continuous_config_provider=_continuous_provider,
        )
        sup = LifeSupervisor(
            memory=mem,
            runner=runner,
            sink=sink,
            config=sup_cfg,
            engineer_model=cfg.engineer_model,
            reviewer_model=cfg.reviewer_model,
            critic_runner=getattr(runner, "backend", None),
        )

        log.info(
            "daemon: ready (life_dir=%s backend=%s pid=%d)",
            cfg.life_dir, cfg.backend, os.getpid(),
        )
        # Use the LifeStderrSink shape only inside ``run`` if verbose
        # debug ever needed; default sink emits to log.
        del LifeStderrSink

        # Start the Telegram inbound command poller (daemon thread — dies
        # with the process). Accepts /add, /status, /nudge, /start, /stop.
        try:
            from ..life.telegram_bot import TelegramPoller
            tg_poller = TelegramPoller(
                life_dir=cfg.life_dir, stop_event=self._stop,
            )
            tg_poller.start()
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to start telegram poller; continuing")

        while not self._stop.is_set():
            try:
                summary = sup.run()
                # When planner declares project done, persist to disk
                # so we don't re-plan the same objective next loop.
                if summary.get("stopped_by") == "project_done":
                    write_continuous_config(
                        cfg.life_dir,
                        enabled=False,
                        objective=sup.config.continuous_objective,
                        done_reason="planner declared project done",
                    )
                elif summary.get("stopped_by") == "planner_unavailable":
                    write_continuous_config(
                        cfg.life_dir,
                        enabled=False,
                        objective=sup.config.continuous_objective,
                    )
            except Exception:  # noqa: BLE001
                log.exception("daemon: drain pass raised; sleeping and retrying")
            # Reset per-run counters so future drain passes work.
            sup._missions_started = 0
            sup._planning_cycles = 0
            if self._stop.is_set():
                break
            self._stop.wait(timeout=cfg.poll_interval)

        log.info(
            "daemon: stopping cleanly (uptime=%.1fs missions=%d)",
            time.time() - (self._started_at or time.time()),
            self._missions_completed,
        )
        return 0


def _runner_namespace(cfg: LifeWorkerConfig) -> Any:
    """Build the argparse-shaped namespace ``build_life_runner`` expects."""
    import argparse
    ns = argparse.Namespace()
    ns.backend = cfg.backend
    ns.engineer_model = cfg.engineer_model
    ns.reviewer_model = cfg.reviewer_model
    ns.scientist_model = cfg.scientist_model
    ns.skills_dir = os.environ.get(
        "ARGUS_SKILL_SKILLS_DIR",
        str(Path.home() / ".argus-skill" / "skills"),
    )
    ns.workdir = os.environ.get("ARGUS_SKILL_WORKDIR")
    ns.max_rounds = int(os.environ.get("ARGUS_SKILL_MAX_ROUNDS", "500"))
    ns.plan_mode = os.environ.get("ARGUS_SKILL_PLAN_MODE", "auto")
    ns.plan_model = os.environ.get("ARGUS_SKILL_PLAN_MODEL")
    ns.check = []
    ns.color = None
    ns.verbose = False
    ns.quiet = True
    return ns


class _DaemonSink:
    """Minimal sink: counts mission completions, forwards progress to
    the Telegram live-streaming reporter, logs everything else."""

    def __init__(self, worker: LifeWorker, stream_reporter: Any = None) -> None:
        self._worker = worker
        self._stream_reporter = stream_reporter

    def handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type") or event.get("kind") or ""
        if kind in (
            "life.mission.done",
            "life.mission.completed",
            "life.mission.failed",
            "life.mission.skipped",
        ):
            self._worker._missions_completed += 1
        # Forward to Telegram live-streaming reporter (non-blocking)
        if self._stream_reporter is not None:
            try:
                self._stream_reporter.on_event(event)
            except Exception:  # noqa: BLE001
                pass
        log.debug("daemon event: %s %s", kind, event)


# ---------------------------------------------------------------------------
# PID lock + status
# ---------------------------------------------------------------------------

def _daemon_pid_path(life_dir: Path) -> Path:
    return life_dir / "daemon.pid"


def _daemon_status_path(life_dir: Path) -> Path:
    return life_dir / "daemon.status.json"


def _daemon_log_path(life_dir: Path, override: Path | None = None) -> Path:
    return override if override is not None else life_dir / "daemon.log"


@dataclass
class DaemonStatus:
    alive: bool
    pid: int | None
    started_at_iso: str | None
    uptime_seconds: float | None
    life_dir: Path
    backend: str | None = None
    pid_path: Path | None = None


def read_daemon_status(life_dir: Path | None = None) -> DaemonStatus:
    """Read the daemon's pid file and return a structured status.

    ``alive=True`` only if both the pid file exists AND the process is
    still running (verified via ``os.kill(pid, 0)``). A stale pid file
    from a hard kill returns ``alive=False`` so callers know the lock
    is reclaimable.
    """
    life_dir = Path(life_dir).expanduser() if life_dir else default_life_dir()
    pid_path = _daemon_pid_path(life_dir)
    if not pid_path.exists():
        return DaemonStatus(
            alive=False, pid=None, started_at_iso=None,
            uptime_seconds=None, life_dir=life_dir, pid_path=pid_path,
        )
    try:
        pid = int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return DaemonStatus(
            alive=False, pid=None, started_at_iso=None,
            uptime_seconds=None, life_dir=life_dir, pid_path=pid_path,
        )
    alive = _process_alive(pid)
    started_iso: str | None = None
    backend: str | None = None
    uptime: float | None = None
    sidecar = _daemon_status_path(life_dir)
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            started_iso = data.get("started_at_iso")
            backend = data.get("backend")
            if started_iso:
                started_dt = datetime.fromisoformat(started_iso)
                uptime = (datetime.now(timezone.utc) - started_dt).total_seconds()
        except Exception:  # noqa: BLE001
            pass
    return DaemonStatus(
        alive=alive,
        pid=pid if alive else None,
        started_at_iso=started_iso,
        uptime_seconds=uptime,
        life_dir=life_dir,
        backend=backend,
        pid_path=pid_path,
    )


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stop_daemon(life_dir: Path | None = None, *, timeout: float = 10.0) -> int:
    """Send SIGTERM to the running daemon and wait up to ``timeout`` for exit.

    Returns 0 on graceful stop, 1 if no daemon was running, 2 on timeout.
    """
    status = read_daemon_status(life_dir)
    if not status.alive or status.pid is None:
        sys.stderr.write("argus-skill: no daemon is running for this life-dir.\n")
        return 1
    pid = status.pid
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return 1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            sys.stdout.write(f"argus-skill: daemon (pid {pid}) stopped.\n")
            return 0
        time.sleep(0.1)
    sys.stderr.write(
        f"argus-skill: daemon (pid {pid}) did not exit within {timeout:.1f}s. "
        "Send SIGKILL manually if needed.\n"
    )
    return 2


# ---------------------------------------------------------------------------
# Detach (POSIX double-fork)
# ---------------------------------------------------------------------------

def spawn_detached_daemon(config: LifeWorkerConfig) -> int:
    """Fork a detached background process running the worker, then exit.

    Returns 0 on successful spawn, 2 if a daemon is already running.

    Uses the standard double-fork idiom to fully detach from the
    controlling terminal and become a session leader. The grandchild
    inherits no fds we care about, redirects std{in,out,err} to the
    log file, acquires the daemon pid lock, writes the status sidecar,
    and finally enters :meth:`LifeWorker.run_forever`.
    """
    # Pre-flight: refuse to spawn if a live daemon is already there.
    existing = read_daemon_status(config.life_dir)
    if existing.alive and existing.pid is not None:
        sys.stderr.write(
            f"argus-skill: daemon already running for this life-dir "
            f"(pid={existing.pid}, lock={existing.pid_path}).\n"
        )
        return 2
    config.life_dir.mkdir(parents=True, exist_ok=True)
    log_path = _daemon_log_path(config.life_dir, config.log_path)
    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)

    # First fork.
    pid = os.fork()
    if pid > 0:
        # Parent waits briefly so we can confirm the daemon really came
        # up before printing success.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pid_path.exists() and status_path.exists():
                try:
                    written_pid = int(pid_path.read_text().strip())
                except (OSError, ValueError):
                    written_pid = 0
                if written_pid and _process_alive(written_pid):
                    sys.stdout.write(
                        f"argus-skill: daemon started (pid {written_pid}, "
                        f"life_dir={config.life_dir}, log={log_path}).\n"
                    )
                    return 0
            time.sleep(0.1)
        sys.stderr.write(
            "argus-skill: daemon fork succeeded but child did not write its "
            f"pid file within 5s. Check {log_path} for errors.\n"
        )
        return 2

    # First child — become session leader.
    try:
        os.setsid()
    except OSError:
        pass

    # Second fork — guarantee no controlling TTY can be reacquired.
    try:
        pid2 = os.fork()
    except OSError:
        pid2 = -1
    if pid2 > 0:
        os._exit(0)

    # Grandchild: this is the daemon. Redirect std fds to the log file.
    os.chdir("/")
    os.umask(0o077)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log_fd, sys.stdout.fileno())
    os.dup2(log_fd, sys.stderr.fileno())
    os.close(log_fd)
    devnull_fd = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull_fd, sys.stdin.fileno())
    os.close(devnull_fd)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Acquire the daemon pid lock. If a competing daemon raced us and
    # got it first we exit cleanly — the parent's pre-flight is just
    # an optimization, the lock is the real guarantee.
    try:
        lock = acquire_global_daemon_lock(pid_path=pid_path)
    except DaemonAlreadyRunning as exc:
        log.error("daemon: another daemon already holds the lock (pid=%s)", exc.pid)
        os._exit(2)

    # Write the status sidecar so ``read_daemon_status`` knows when we
    # started + which backend we're on.
    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at_iso": started_iso,
            "backend": config.backend,
            "life_dir": str(config.life_dir),
        }))
    except OSError:
        log.exception("daemon: failed to write status sidecar")

    try:
        worker = LifeWorker(config)
        rc = worker.run_forever()
    except Exception:  # noqa: BLE001
        log.exception("daemon: fatal error")
        rc = 1
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            log.exception("daemon: failed to release lock")
        try:
            status_path.unlink()
        except OSError:
            pass

    os._exit(rc)


def run_foreground(config: LifeWorkerConfig) -> int:
    """Run the worker in the foreground (for systemd / debugging).

    Same lock + status sidecar as the detached path, but logs go to
    stderr and SIGINT/SIGTERM stop the process directly.
    """
    config.life_dir.mkdir(parents=True, exist_ok=True)
    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)
    try:
        lock = acquire_global_daemon_lock(pid_path=pid_path)
    except DaemonAlreadyRunning as exc:
        sys.stderr.write(
            f"argus-skill: daemon already running for this life-dir "
            f"(pid={exc.pid}, lock={exc.lock_path}).\n"
        )
        return 2

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at_iso": started_iso,
            "backend": config.backend,
            "life_dir": str(config.life_dir),
        }))
    except OSError:
        log.exception("daemon-fg: failed to write status sidecar")

    try:
        worker = LifeWorker(config)
        return worker.run_forever()
    finally:
        try:
            lock.release()
        except Exception:  # noqa: BLE001
            log.exception("daemon-fg: failed to release lock")
        try:
            status_path.unlink()
        except OSError:
            pass
