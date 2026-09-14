from __future__ import annotations

import time
from pathlib import Path

from argus.daemon import health as daemon_health
from argus.daemon.health import DaemonHealthTracker, read_daemon_health


def test_atomic_health_write_retries_transient_windows_permission_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "daemon.health.json"
    real_replace = daemon_health.os.replace
    attempts = 0
    sleeps: list[float] = []

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(13, "simulated Windows sharing conflict", destination)
        return real_replace(source, destination)

    monkeypatch.setattr(daemon_health, "_running_on_windows", lambda: True, raising=False)
    monkeypatch.setattr(daemon_health.os, "replace", flaky_replace)
    monkeypatch.setattr(daemon_health.time, "sleep", sleeps.append)

    daemon_health._atomic_write(path, {"schema_version": 1, "phase": "idle"})

    assert attempts == 2
    assert sleeps and sleeps[0] > 0
    assert path.exists()


def test_active_daemon_without_progress_is_reported_stalled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_STALL_SECONDS", "10")
    tracker = DaemonHealthTracker(tmp_path, pid=123)
    old = time.time() - 20
    tracker.observe({"type": "round.start", "ts": old})

    health = read_daemon_health(tmp_path, pid=123, alive=True)

    assert health["state"] == "stalled"
    assert health["stalled"] is True
    assert health["last_progress_event"] == "round.start"


def test_intentional_wait_is_not_mislabeled_as_stalled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_STALL_SECONDS", "10")
    tracker = DaemonHealthTracker(tmp_path, pid=123)
    old = time.time() - 20
    tracker.observe({"type": "round.start", "ts": old - 1})
    tracker.observe({"type": "life.planner.waiting", "ts": old})

    health = read_daemon_health(tmp_path, pid=123, alive=True)

    assert health["state"] == "waiting"
    assert health["stalled"] is False


def test_progress_event_clears_stall_age(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_STALL_SECONDS", "10")
    tracker = DaemonHealthTracker(tmp_path, pid=123)
    tracker.observe({"type": "round.start", "ts": time.time() - 20})
    tracker.observe({"type": "engineer.progress", "ts": time.time()})

    health = read_daemon_health(tmp_path, pid=123, alive=True)

    assert health["state"] == "active"
    assert health["stalled"] is False
    assert health["last_progress_event"] == "engineer.progress"


def test_repeated_active_retries_do_not_reset_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_STALL_SECONDS", "10")
    tracker = DaemonHealthTracker(tmp_path, pid=123)
    old = time.time() - 20
    tracker.observe({"type": "life.planner.start", "ts": old})
    tracker.observe({"type": "provider.request.started", "ts": time.time()})

    health = read_daemon_health(tmp_path, pid=123, alive=True)

    assert health["state"] == "stalled"
    assert health["last_progress_event"] == "life.planner.start"


def test_degraded_planner_retry_does_not_reset_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_STALL_SECONDS", "10")
    tracker = DaemonHealthTracker(tmp_path, pid=123)
    old = time.time() - 20
    tracker.observe({"type": "life.planner.start", "ts": old})
    tracker.observe({"type": "life.planner.error", "ts": old + 1})
    tracker.observe({"type": "life.planner.start", "ts": time.time()})

    health = read_daemon_health(tmp_path, pid=123, alive=True)

    assert health["state"] == "stalled"
    assert health["last_progress_event"] == "life.planner.start"
    assert health["last_progress_at"] == old


def test_ready_daemon_transitions_out_of_starting(tmp_path: Path) -> None:
    tracker = DaemonHealthTracker(tmp_path, pid=123)

    tracker.mark_ready()

    health = read_daemon_health(tmp_path, pid=123, alive=True)
    assert health["state"] == "idle"
    assert health["last_progress_event"] == "life.daemon.ready"
