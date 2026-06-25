"""Global daemon singleton lock.

Phase 1 of the unification refactor introduces ONE global daemon process
per user (running under ``~/.argus-skill/bus/``). To prevent two daemons
fighting over the same command/outbox files we use an OS-level advisory
file lock plus a PID file:

* ``acquire_global_daemon_lock()`` opens ``daemon.pid`` (creating it),
  takes a non-blocking exclusive ``flock`` on it, writes the current
  pid, and returns a :class:`DaemonLock` whose ``release()`` cleans up.
* If another live daemon already holds the lock, the call raises
  :class:`DaemonAlreadyRunning` carrying the holder's pid.
* If the pid file is stale (the holder crashed without releasing the
  lock), the OS will let us acquire it. We then overwrite the file with
  our own pid.

This is the singleton daemon lock: one live daemon per project. (A legacy
``daemon/token_lock.py`` Telegram-token-uniqueness lock once sat alongside it;
that module had no importers and has been removed.)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths as core_paths

fcntl: Any = None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    pass
else:
    fcntl = _fcntl

msvcrt: Any = None
try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    pass
else:
    msvcrt = _msvcrt

log = logging.getLogger(__name__)

__all__ = [
    "DaemonAlreadyRunning",
    "DaemonLock",
    "acquire_global_daemon_lock",
    "read_daemon_pid",
    "is_pid_running",
]


class DaemonAlreadyRunning(RuntimeError):
    """Raised when another live daemon already holds the global lock."""

    def __init__(self, pid: int | None, lock_path: Path) -> None:
        msg = (
            f"another argus-skill daemon is already running (pid={pid}, "
            f"lock={lock_path})"
        )
        super().__init__(msg)
        self.pid = pid
        self.lock_path = lock_path


@dataclass
class DaemonLock:
    """Held resource representing the global daemon singleton claim.

    Call :meth:`release` (or use as a context manager) when shutting
    down. The flock is also released automatically when the holding
    process exits, but explicit release lets unit tests tear down
    cleanly.
    """

    pid: int
    pid_path: Path
    fd: int

    def release(self) -> None:
        try:
            _unlock_file(self.fd)
        finally:
            try:
                os.close(self.fd)
            except OSError:
                pass
        try:
            current = read_daemon_pid(self.pid_path)
        except OSError:
            current = None
        if current == self.pid:
            try:
                self.pid_path.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> "DaemonLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_global_daemon_lock(
    *,
    pid_path: Path | str | None = None,
) -> DaemonLock:
    """Acquire the singleton lock; raise :class:`DaemonAlreadyRunning` if held.

    ``pid_path`` is ``~/.argus-skill/bus/daemon.pid`` by default. Passing
    a custom path is intended for tests; production code should use the
    default so multiple agent invocations in the same user account
    coordinate via the same file.
    """
    target = Path(pid_path).expanduser() if pid_path else core_paths.daemon_pid_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_file(fd)
    except OSError:
        existing_pid: int | None = None
        try:
            existing_pid = read_daemon_pid(target)
        except OSError:
            pass
        os.close(fd)
        raise DaemonAlreadyRunning(existing_pid, target) from None

    pid = os.getpid()
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{pid}\n".encode("ascii"))
        try:
            os.fsync(fd)
        except OSError:
            pass
    except OSError:
        log.warning("daemon-lock: failed to write pid into %s", target)

    return DaemonLock(pid=pid, pid_path=target, fd=fd)


def read_daemon_pid(pid_path: Path | str | None = None) -> int | None:
    """Read the pid recorded in ``daemon.pid``; return None if missing/garbage."""
    target = Path(pid_path).expanduser() if pid_path else core_paths.daemon_pid_path()
    try:
        raw = target.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw:
        return None
    try:
        value = int(raw.splitlines()[0])
    except ValueError:
        return None
    return value if value > 0 else None


def is_pid_running(pid: int) -> bool:
    """Return True if a process with the given pid currently exists.

    Mirrors the helper in ``daemon/bus.py`` but kept local to avoid an
    import cycle (paths/bus shouldn't depend on daemon/*).
    """
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows fallback
        # Conservative: assume alive; the flock acquisition is the
        # authoritative liveness check on POSIX too.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it (different uid). Still alive.
        return True
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Cross-platform flock primitives
# ---------------------------------------------------------------------------

def _lock_file(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows
        if msvcrt is None:
            raise OSError("msvcrt not available")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return
    if fcntl is None:  # pragma: no cover - safety net
        raise OSError("fcntl not available")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fd: int) -> None:
    if os.name == "nt":  # pragma: no cover - Windows
        if msvcrt is None:
            return
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    if fcntl is None:  # pragma: no cover - safety net
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
