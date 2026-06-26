"""The shared Manager codex session (manager/_core._ManagerSession): the lock
acquire is BOUNDED (no whole-turn cross-process starvation) and the session tid
persists + resumes. The recovery fallback on a session-mode error is covered by
tests/manager/test_manager_session.py.
"""
from __future__ import annotations

import fcntl
import time
from pathlib import Path

from argus_skill.manager._core import _acquire_session_lock, _ManagerSession


class _CountingRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ANN001
        self.calls.append({"resume": resume_thread_id, "label": run_label})

        class _R:
            thread_id = "t-new"

        return _R()


def test_session_tid_is_persisted_and_resumed(tmp_path: Path) -> None:
    r = _CountingRunner()
    sess = _ManagerSession(r, tmp_path)
    res = sess.run_exec(prompt="p", options=None, run_label="manager-x")
    assert res.thread_id == "t-new"
    assert r.calls[0]["resume"] is None  # first turn, no session yet
    sess.run_exec(prompt="p2", options=None, run_label="manager-x")
    assert r.calls[1]["resume"] == "t-new"  # second turn resumes the persisted tid


def test_lock_acquire_is_bounded_then_succeeds_when_free(tmp_path: Path) -> None:
    # The fix: LOCK_EX is acquired non-blocking with a bounded wait, so a hung peer
    # turn can't freeze the other process indefinitely.
    lock = tmp_path / "l.lock"
    holder = lock.open("a+b")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        waiter = lock.open("a+b")
        t0 = time.monotonic()
        assert _acquire_session_lock(waiter, timeout=0.4) is False  # bounded, no hang
        assert time.monotonic() - t0 >= 0.3  # actually waited ~the budget, didn't block forever
        waiter.close()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
    free = lock.open("a+b")
    assert _acquire_session_lock(free, timeout=0.4) is True  # acquires once free
    free.close()
