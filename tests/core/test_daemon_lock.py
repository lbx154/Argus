"""Tests for the global daemon singleton lock."""
from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import pytest

from argus.core.daemon_lock import (
    DaemonAlreadyRunning,
    acquire_global_daemon_lock,
    is_pid_running,
    read_daemon_pid,
)


def test_acquire_writes_pid_and_creates_dir(tmp_path: Path) -> None:
    pid_path = tmp_path / "bus" / "daemon.pid"
    lock = acquire_global_daemon_lock(pid_path=pid_path)
    try:
        assert pid_path.exists()
        assert read_daemon_pid(pid_path) == os.getpid()
        assert lock.pid == os.getpid()
        assert lock.pid_path == pid_path
    finally:
        lock.release()


def test_release_removes_pid_file(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    lock = acquire_global_daemon_lock(pid_path=pid_path)
    lock.release()
    assert not pid_path.exists()


def test_release_does_not_remove_other_holders_pid_file(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    lock = acquire_global_daemon_lock(pid_path=pid_path)
    # Simulate another writer overwriting the pid (e.g. a follow-up
    # daemon after we crash). Our release must leave that file alone.
    pid_path.write_text("99999\n", encoding="ascii")
    lock.release()
    assert pid_path.exists()
    assert read_daemon_pid(pid_path) == 99999


def test_context_manager_releases(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    with acquire_global_daemon_lock(pid_path=pid_path):
        assert pid_path.exists()
    assert not pid_path.exists()


def _hold_then_acquire(pid_path: str, started: Any, q: Any) -> None:
    """Subprocess body: try to acquire lock and report outcome."""
    try:
        lock = acquire_global_daemon_lock(pid_path=Path(pid_path))
    except DaemonAlreadyRunning as exc:
        q.put(("denied", exc.pid))
        return
    try:
        q.put(("acquired", lock.pid))
        started.wait(5.0)
    finally:
        lock.release()


def test_second_acquire_fails_while_first_held(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    lock = acquire_global_daemon_lock(pid_path=pid_path)
    try:
        # Use the host default so this also verifies the Windows msvcrt lock
        # from a real spawned process instead of requiring POSIX ``fork``.
        ctx = multiprocessing.get_context()
        started: Any = ctx.Event()
        q: Any = ctx.Queue()
        proc = ctx.Process(
            target=_hold_then_acquire, args=(str(pid_path), started, q)
        )
        proc.start()
        started.set()
        outcome = q.get(timeout=10.0)
        proc.join(timeout=10.0)
        assert outcome[0] == "denied"
        assert outcome[1] == os.getpid()
    finally:
        lock.release()


def test_acquire_succeeds_after_holder_releases(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    first = acquire_global_daemon_lock(pid_path=pid_path)
    first.release()
    second = acquire_global_daemon_lock(pid_path=pid_path)
    try:
        assert read_daemon_pid(pid_path) == os.getpid()
    finally:
        second.release()


def test_acquire_after_stale_pidfile(tmp_path: Path) -> None:
    """A leftover pid file from a crashed daemon must not block us."""
    pid_path = tmp_path / "daemon.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("999999\n", encoding="ascii")  # not running
    lock = acquire_global_daemon_lock(pid_path=pid_path)
    try:
        assert read_daemon_pid(pid_path) == os.getpid()
    finally:
        lock.release()


def test_read_daemon_pid_handles_garbage(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text("not-a-pid\n", encoding="ascii")
    assert read_daemon_pid(pid_path) is None

    pid_path.write_text("", encoding="ascii")
    assert read_daemon_pid(pid_path) is None

    pid_path.write_text("0\n", encoding="ascii")
    assert read_daemon_pid(pid_path) is None


def test_read_daemon_pid_missing_file(tmp_path: Path) -> None:
    assert read_daemon_pid(tmp_path / "nope.pid") is None


def test_is_pid_running_self() -> None:
    assert is_pid_running(os.getpid()) is True


@pytest.mark.skipif(
    not hasattr(os, "fork") or not Path("/proc/self/stat").is_file(),
    reason="requires Linux procfs and os.fork",
)
def test_is_pid_running_excludes_unreaped_linux_zombie() -> None:
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child exits immediately
        os._exit(0)
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{pid}/stat")
            if stat.is_file() and ") Z " in stat.read_text(errors="replace"):
                break
            time.sleep(0.01)
        assert is_pid_running(pid) is False
    finally:
        os.waitpid(pid, 0)


def test_is_pid_running_dead_pid() -> None:
    # Above Linux's maximum configurable pid_max and invalid on other platforms.
    assert is_pid_running(2_147_483_647) is False


def test_is_pid_running_invalid() -> None:
    assert is_pid_running(0) is False
    assert is_pid_running(-1) is False
