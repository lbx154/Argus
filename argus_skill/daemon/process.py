"""Detached and foreground daemon process lifecycle."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

from ..core.daemon_lock import DaemonAlreadyRunning, acquire_global_daemon_lock
from .state import (
    _daemon_log_path,
    _daemon_pid_path,
    _daemon_status_path,
    _daemon_status_payload,
    _descendant_pids,
    _new_boot_id,
    _point_active_daemon_log,
    _redirect_std_to_log,
    _terminate_windows_process_tree,
    read_daemon_status,
)

log = logging.getLogger(__name__)

_DAEMON_PUBLISH_TIMEOUT_SECONDS = 5.0
_WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS = 180.0
_DAEMON_STABILITY_SECONDS = 0.5
_DAEMON_POLL_INTERVAL_SECONDS = 0.1


def _wait_for_stable_daemon_status(
    life_dir,
    *,
    publish_timeout_s: float = _DAEMON_PUBLISH_TIMEOUT_SECONDS,
    stable_for_s: float = _DAEMON_STABILITY_SECONDS,
    poll_interval_s: float = _DAEMON_POLL_INTERVAL_SECONDS,
):
    """Return a daemon status only after one PID stays continuously alive."""
    publish_deadline = time.monotonic() + publish_timeout_s
    stable_since: float | None = None
    stable_pid: int | None = None

    while True:
        status = read_daemon_status(life_dir)
        now = time.monotonic()
        valid = (
            status.alive
            and status.pid is not None
            and not status.status_read_error
        )
        if valid:
            if stable_since is None or status.pid != stable_pid:
                stable_since = now
                stable_pid = status.pid
            elif now - stable_since >= stable_for_s:
                return status
        elif stable_since is not None:
            return None
        elif now >= publish_deadline:
            return None

        time.sleep(poll_interval_s)


def _windows_daemon_command(config: Any) -> list[str]:
    """Re-enter the active package/binary as one foreground worker."""
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(["-m", "argus_skill"])
    global_root = config.global_root or config.life_dir.parent.parent
    command.extend(
        [
            "--daemon-fg",
            "--life-dir",
            str(global_root),
            "--resume",
            config.life_dir.name,
            "--backend",
            str(config.backend),
        ]
    )
    if config.continuous:
        objective_file = getattr(config, "continuous_objective_file", None)
        if objective_file is not None:
            command.extend(["--continuous", "--objective-file", str(objective_file)])
        else:
            command.extend(["--continuous", "--objective", config.continuous_objective])
    if config.resume_continuous:
        command.append("--resume-continuous")
    if not config.continuous_open_ended:
        command.append("--bounded")
    command.extend(["--mission-width", str(getattr(config, "mission_width", 2))])
    return command


def _windows_runtime_belongs_to_launcher(
    launcher_pid: int,
    runtime_pid: int,
) -> bool:
    """Prove that a published worker PID belongs to the process we spawned.

    A Windows virtual-environment ``python.exe`` is a launcher stub. Its PID is
    the one returned by :class:`subprocess.Popen`, while the base interpreter
    child acquires ``daemon.pid`` and publishes ``daemon.status.json``. Requiring
    PID equality therefore rejects a healthy source-checkout worker. Keep the
    foreign-status protection by accepting only the launcher itself or one of
    its current descendants.
    """
    if runtime_pid == launcher_pid:
        return True
    return runtime_pid in _descendant_pids(launcher_pid)


def _reap_failed_windows_spawn(process: subprocess.Popen[Any]) -> None:
    """Reclaim the exact worker tree after a failed publication handshake."""
    pid = int(process.pid)
    if process.poll() is None:
        terminated = _terminate_windows_process_tree(
            pid,
            identity_check=lambda: process.pid == pid and process.poll() is None,
        )
        if not terminated and process.poll() is None:
            # Popen owns an OS handle to this exact process, so this fallback
            # cannot target a reused PID. The tree helper normally handles all
            # descendants; terminate() is the final root cleanup if Windows
            # denied process enumeration.
            process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            log.error("Windows worker pid=%s resisted startup cleanup", pid)


def _spawn_windows_background_process(
    config: Any,
    *,
    pid_path,
    status_path,
    log_path,
    spawn_lock_fd: int | None,
    release_spawn_lock: Callable[..., None],
    quiet: bool,
) -> int:
    """Launch the terminal-scoped Windows worker without POSIX fork()."""
    env = os.environ.copy()
    env["ARGUS_BINARY_MODE"] = "cli"
    # Windows commonly inherits a CP936 console.  The detached worker emits
    # Unicode status glyphs and must not crash before publishing daemon status.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log_handle:
            process = subprocess.Popen(
                _windows_daemon_command(config),
                cwd=config.project_workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creationflags,
            )
        deadline = time.monotonic() + _WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS
        exit_rc: int | None = None
        stable_since: float | None = None
        stable_pid: int | None = None
        while time.monotonic() < deadline:
            if pid_path.exists() and status_path.exists():
                status = read_daemon_status(config.life_dir)
                runtime_pid = int(status.pid or 0)
                if (
                    status.alive
                    and runtime_pid > 0
                    and _windows_runtime_belongs_to_launcher(
                        int(process.pid),
                        runtime_pid,
                    )
                    and not status.status_read_error
                    and process.poll() is None
                ):
                    now = time.monotonic()
                    if stable_since is None or stable_pid != runtime_pid:
                        stable_since = now
                        stable_pid = runtime_pid
                    elif now - stable_since >= _DAEMON_STABILITY_SECONDS:
                        if not quiet:
                            sys.stdout.write(
                                f"argus-skill: daemon started (pid {status.pid}, "
                                f"life_dir={config.life_dir}, log={log_path}).\n"
                            )
                        return 0
                else:
                    stable_since = None
                    stable_pid = None
            if process.poll() is not None:
                exit_rc = process.returncode
                break
            time.sleep(0.1)
        if exit_rc is None:
            _reap_failed_windows_spawn(process)
        if not quiet:
            if exit_rc is not None:
                sys.stderr.write(
                    "argus-skill: Windows worker exited before publishing "
                    f"daemon status (rc={exit_rc}). Check {log_path} for errors.\n"
                )
            else:
                sys.stderr.write(
                    "argus-skill: Windows worker did not publish its status within "
                    f"{_WINDOWS_DAEMON_PUBLISH_TIMEOUT_SECONDS:g}s. "
                    f"Check {log_path} for errors.\n"
                )
        # A zero exit before the PID/status handshake is still a failed worker:
        # no process remains to execute queued work. Preserve actionable
        # non-zero child codes, but never turn an unverified clean exit into a
        # successful executor start.
        return int(exit_rc) if exit_rc not in {None, 0} else 2
    finally:
        release_spawn_lock(spawn_lock_fd)

def spawn_detached_process(
    config: Any,
    *,
    worker_factory: Callable[[Any], Any],
    acquire_spawn_lock: Callable[[Any], int | None],
    release_spawn_lock: Callable[..., None],
    max_active_daemons: Callable[[Any], int],
    active_daemon_count: Callable[[Any], int],
    workspace_start_error: Callable[[Any], str] | None = None,
    acquire_workspace_lease: Callable[[Any], int | None] | None = None,
    release_workspace_lease: Callable[..., None] | None = None,
    quiet: bool = False,
) -> int:
    """Fork a detached background process running the worker, then exit.

    Returns 0 on successful spawn, 2 if a daemon is already running.

    Uses the standard double-fork idiom to fully detach from the
    controlling terminal and become a session leader. The grandchild
    inherits no fds we care about, redirects std{in,out,err} to the
    log file, acquires the daemon pid lock, writes the status sidecar,
    and finally enters :meth:`LifeWorker.run_forever`.
    """
    spawn_lock_fd = acquire_spawn_lock(config)
    workspace_lease_fd: int | None = None
    delegated_windows_spawn = False
    try:
        # Count and fork while holding one host-wide admission lock. A second
        # launcher cannot observe the same pre-start count before this child has
        # published its pid/status sidecars.
        existing = read_daemon_status(config.life_dir)
        if existing.alive and existing.pid is not None:
            if not quiet:
                sys.stderr.write(
                    f"argus-skill: daemon already running for this life-dir "
                    f"(pid={existing.pid}, lock={existing.pid_path}).\n"
                )
            release_spawn_lock(spawn_lock_fd)
            return 2
        workspace_error = (
            workspace_start_error(config)
            if workspace_start_error is not None
            else ""
        )
        if workspace_error:
            if not quiet:
                sys.stderr.write(f"argus-skill: {workspace_error}.\n")
            release_spawn_lock(spawn_lock_fd)
            return 3
        daemon_limit = max_active_daemons(config)
        active_count = active_daemon_count(config)
        if daemon_limit > 0 and active_count >= daemon_limit:
            if not quiet:
                sys.stderr.write(
                    f"argus-skill: refusing to start another daemon: host-wide "
                    f"active-daemon cap {daemon_limit} reached ({active_count} live). "
                    "Stop an existing project or raise "
                    "ARGUS_SKILL_MAX_ACTIVE_DAEMONS explicitly.\n"
                )
            release_spawn_lock(spawn_lock_fd)
            return 2
        if acquire_workspace_lease is not None and os.name != "nt":
            try:
                workspace_lease_fd = acquire_workspace_lease(config)
            except Exception as exc:  # noqa: BLE001
                if not quiet:
                    sys.stderr.write(f"argus-skill: {exc}.\n")
                release_spawn_lock(spawn_lock_fd)
                return 3
        config.life_dir.mkdir(parents=True, exist_ok=True)
        boot_id = _new_boot_id()
        log_path = _daemon_log_path(config.life_dir, config.log_path, boot_id)
        _point_active_daemon_log(config.life_dir, log_path)
        pid_path = _daemon_pid_path(config.life_dir)
        status_path = _daemon_status_path(config.life_dir)
        if os.name == "nt":
            delegated_windows_spawn = True
            return _spawn_windows_background_process(
                config,
                pid_path=pid_path,
                status_path=status_path,
                log_path=log_path,
                spawn_lock_fd=spawn_lock_fd,
                release_spawn_lock=release_spawn_lock,
                quiet=quiet,
            )
        pid = os.fork()
    except Exception:
        if release_workspace_lease is not None:
            release_workspace_lease(workspace_lease_fd)
        if not delegated_windows_spawn:
            release_spawn_lock(spawn_lock_fd)
        raise
    if pid > 0:
        try:
            # A daemon publishes pid/status before backend readiness. Require
            # continuous liveness so a readiness failure cannot be reported as
            # a successful executor start.
            status = _wait_for_stable_daemon_status(config.life_dir)
            if status is not None:
                if not quiet:
                    sys.stdout.write(
                        f"argus-skill: daemon started (pid {status.pid}, "
                        f"life_dir={config.life_dir}, log={log_path}).\n"
                    )
                return 0
            if not quiet:
                sys.stderr.write(
                    "argus-skill: daemon exited or failed to stabilize during "
                    f"startup. Check {log_path} for errors.\n"
                )
            return 2
        finally:
            if release_workspace_lease is not None:
                # The daemon child inherited the same locked open-file
                # description. Parent closes only its copy; unlocking here
                # would release the child's lifetime lease too.
                release_workspace_lease(workspace_lease_fd, unlock=False)
            release_spawn_lock(spawn_lock_fd)

    # The parent owns admission. Close only this inherited descriptor copy;
    # unlocking here would release the parent's lock before pid publication.
    release_spawn_lock(spawn_lock_fd, unlock=False)

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

    # Close every inherited fd beyond std{in,out,err}. ``os.fork`` (unlike
    # ``subprocess(close_fds=True)``) inherits the WHOLE fd table of whoever
    # called ``spawn_detached_daemon`` — which is often the web server
    # (``argus-skill --web``), whose LISTENING SOCKET would otherwise be kept
    # open here and wedge that port after the web restarts (a real fd leak:
    # connections queue to a daemon that never accepts). The daemon opens every
    # fd it actually needs (pid lock, status sidecar, events) AFTER this point,
    # so dropping the inherited table is safe and correct daemonisation.
    try:
        _keep = {0, 1, 2}
        if workspace_lease_fd is not None:
            _keep.add(workspace_lease_fd)
        for _name in os.listdir("/proc/self/fd"):
            try:
                _fd = int(_name)
            except ValueError:
                continue
            if _fd not in _keep:
                try:
                    os.close(_fd)
                except OSError:
                    pass
    except FileNotFoundError:  # /proc unavailable — bounded fallback
        os.closerange(3, 4096)

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
        status_path.write_text(
            json.dumps(_daemon_status_payload(config, started_at_iso=started_iso))
        )
    except OSError:
        log.exception("daemon: failed to write status sidecar")

    try:
        worker = worker_factory(config)
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
        if release_workspace_lease is not None:
            try:
                release_workspace_lease(workspace_lease_fd)
            except Exception:  # noqa: BLE001
                log.exception("daemon: failed to release workspace lease")

    os._exit(rc)

def run_foreground_process(
    config: Any,
    *,
    worker_factory: Callable[[Any], Any],
    workspace_start_error: Callable[[Any], str] | None = None,
    acquire_workspace_lease: Callable[[Any], int | None] | None = None,
    release_workspace_lease: Callable[..., None] | None = None,
) -> int:
    """Run the worker in the foreground (for systemd / debugging).

    Same lock + status sidecar as the detached path, but logs go to
    stderr and SIGINT/SIGTERM stop the process directly.
    """
    workspace_error = (
        workspace_start_error(config)
        if workspace_start_error is not None
        else ""
    )
    if workspace_error:
        sys.stderr.write(f"argus-skill: {workspace_error}.\n")
        return 3
    workspace_lease_fd: int | None = None
    if acquire_workspace_lease is not None:
        try:
            workspace_lease_fd = acquire_workspace_lease(config)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"argus-skill: {exc}.\n")
            return 3

    config.life_dir.mkdir(parents=True, exist_ok=True)
    pid_path = _daemon_pid_path(config.life_dir)
    status_path = _daemon_status_path(config.life_dir)
    try:
        lock = acquire_global_daemon_lock(pid_path=pid_path)
    except DaemonAlreadyRunning as exc:
        if release_workspace_lease is not None:
            release_workspace_lease(workspace_lease_fd)
        sys.stderr.write(
            f"argus-skill: daemon already running for this life-dir "
            f"(pid={exc.pid}, lock={exc.lock_path}).\n"
        )
        return 2

    # We own the daemon — segment THIS boot's output into its own per-boot log
    # (fixes --daemon-fg previously writing no file); the daemon.log symlink
    # points here. keep_console tees Python logs to the original stderr (terminal
    # / journald) so an interactive fg run still shows progress.
    boot_id = _new_boot_id()
    log_path = _daemon_log_path(config.life_dir, config.log_path, boot_id)
    _point_active_daemon_log(config.life_dir, log_path)
    saved_console = _redirect_std_to_log(log_path, keep_console=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    if saved_console is not None:
        try:
            _console = os.fdopen(saved_console, "w", buffering=1)
            _handler = logging.StreamHandler(_console)
            _handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            logging.getLogger().addHandler(_handler)
        except OSError:
            pass

    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        status_path.write_text(
            json.dumps(_daemon_status_payload(config, started_at_iso=started_iso))
        )
    except OSError:
        log.exception("daemon-fg: failed to write status sidecar")

    try:
        worker = worker_factory(config)
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
        if release_workspace_lease is not None:
            try:
                release_workspace_lease(workspace_lease_fd)
            except Exception:  # noqa: BLE001
                log.exception("daemon-fg: failed to release workspace lease")

__all__ = ["run_foreground_process", "spawn_detached_process"]
