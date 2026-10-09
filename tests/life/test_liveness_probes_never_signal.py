"""Liveness probes must not send signal 0.

On Windows CPython maps every non-console signal, including 0, to
TerminateProcess, so ``os.kill(pid, 0)`` kills the process it was meant to
check. A Web status poll used that probe on the daemon that owned a running
mission reflection and silently terminated it.
"""
import os
from pathlib import Path

import pytest

from argus.apps.cli import _follow
from argus.core import daemon_lock
from argus.life import answer_learning
from argus.tools import gpu_lease


@pytest.fixture
def no_signals(monkeypatch):
    def forbidden(pid, sig):
        raise AssertionError(f"os.kill({pid}, {sig}) would terminate the process on Windows")

    monkeypatch.setattr(os, "kill", forbidden)


@pytest.mark.parametrize("owner_alive,expected", [(True, "running"), (False, "failed")])
def test_learning_status_checks_the_reflection_owner_without_signalling_it(
    tmp_path, monkeypatch, no_signals, owner_alive, expected,
):
    asked = []
    monkeypatch.setattr(answer_learning, "is_pid_running", lambda pid: asked.append(pid) or owner_alive)
    seen = {}

    def run(_capture):
        seen.update(answer_learning.learning_status(tmp_path, "s-test"))
        return {}

    answer_learning.observe_mission(tmp_path, "s-test", "mission-1", run, None)

    assert asked == [os.getpid()]
    assert [job["status"] for job in seen["jobs"]] == [expected]
    assert seen["pending"] == (1 if owner_alive else 0)


def test_follow_reads_daemon_liveness_without_signalling_it(tmp_path, monkeypatch, no_signals):
    (tmp_path / "daemon.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(daemon_lock, "is_pid_running", lambda pid: pid == 4242)

    assert _follow._daemon_alive_for_events_path(Path(tmp_path) / "events.jsonl") is True


def test_gpu_lease_owner_liveness_never_signals(monkeypatch, no_signals):
    monkeypatch.setattr(daemon_lock, "is_pid_running", lambda pid: pid == 77)

    assert gpu_lease._alive(77) is True
    assert gpu_lease._alive(78) is False
