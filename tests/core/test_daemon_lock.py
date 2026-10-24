"""Tests for the global daemon singleton lock."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from argus.core.daemon_lock import (
    acquire_global_daemon_lock,
    is_pid_running,
    read_daemon_pid,
)


def test_release_does_not_remove_other_holders_pid_file(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    lock = acquire_global_daemon_lock(pid_path=pid_path)
    # Simulate another writer overwriting the pid (e.g. a follow-up
    # daemon after we crash). Our release must leave that file alone.
    pid_path.write_text("99999\n", encoding="ascii")
    lock.release()
    assert pid_path.exists()
    assert read_daemon_pid(pid_path) == 99999


def test_read_daemon_pid_handles_garbage(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text("not-a-pid\n", encoding="ascii")
    assert read_daemon_pid(pid_path) is None

    pid_path.write_text("", encoding="ascii")
    assert read_daemon_pid(pid_path) is None

    pid_path.write_text("0\n", encoding="ascii")
    assert read_daemon_pid(pid_path) is None


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


def test_is_pid_running_invalid() -> None:
    assert is_pid_running(0) is False
    assert is_pid_running(-1) is False
