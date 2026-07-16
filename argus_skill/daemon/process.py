"""Detached and foreground daemon process lifecycle."""

from __future__ import annotations

import json
import logging
import os
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
    _new_boot_id,
    _point_active_daemon_log,
    _redirect_std_to_log,
    read_daemon_status,
)

log = logging.getLogger(__name__)

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
        if acquire_workspace_lease is not None:
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
        pid = os.fork()
    except Exception:
        if release_workspace_lease is not None:
            release_workspace_lease(workspace_lease_fd)
        release_spawn_lock(spawn_lock_fd)
        raise
    if pid > 0:
        try:
            # Parent waits briefly so the admission lock covers pid publication.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if pid_path.exists() and status_path.exists():
                    status = read_daemon_status(config.life_dir)
                    if (
                        status.alive
                        and status.pid is not None
                        and not status.status_read_error
                    ):
                        if not quiet:
                            sys.stdout.write(
                                f"argus-skill: daemon started (pid {status.pid}, "
                                f"life_dir={config.life_dir}, log={log_path}).\n"
                            )
                        return 0
                time.sleep(0.1)
            if not quiet:
                sys.stderr.write(
                    "argus-skill: daemon fork succeeded but child did not write its "
                    f"pid file within 5s. Check {log_path} for errors.\n"
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
) -> int:
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

__all__ = ["run_foreground_process", "spawn_detached_process"]
