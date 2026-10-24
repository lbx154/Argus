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


def test_intentional_wait_is_not_mislabeled_as_stalled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_STALL_SECONDS", "10")
    tracker = DaemonHealthTracker(tmp_path, pid=123)
    old = time.time() - 20
    tracker.observe({"type": "round.start", "ts": old - 1})
    tracker.observe({"type": "life.planner.idle", "ts": old})

    health = read_daemon_health(tmp_path, pid=123, alive=True)

    assert health["state"] == "waiting"
    assert health["stalled"] is False


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
