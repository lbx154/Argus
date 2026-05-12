"""Smoke tests for the 7×24 life worker."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from argus_skill.daemon.life_worker import (
    ContinuousConfigState,
    DaemonStatus,
    LifeWorker,
    LifeWorkerConfig,
    _DaemonSink,
    read_continuous_state,
    read_daemon_status,
    stop_daemon,
)
from argus_skill.life.memory import BacklogItem, LifeMemory


def test_read_daemon_status_returns_not_alive_on_missing_pid(tmp_path: Path) -> None:
    status = read_daemon_status(tmp_path)
    assert isinstance(status, DaemonStatus)
    assert status.alive is False
    assert status.pid is None
    assert status.life_dir == tmp_path


def test_read_daemon_status_detects_stale_pid(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("2000000000\n")
    assert read_daemon_status(tmp_path).alive is False


def test_read_daemon_status_treats_garbage_pid_file_as_dead(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("not-a-number\n")
    s = read_daemon_status(tmp_path)
    assert s.alive is False and s.pid is None


def test_stop_daemon_returns_1_when_no_daemon(tmp_path: Path) -> None:
    assert stop_daemon(tmp_path) == 1


def test_life_worker_drains_backlog_and_stops_on_signal(tmp_path: Path) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path, backend="memory",
        per_mission_cap_usd=10.0, daily_cap_usd=100.0, poll_interval=0.1,
    )
    mem = LifeMemory.open(tmp_path)
    mem.init()
    mem.backlog.add(BacklogItem.new(title="hi", objective="say hi", max_cost_usd=1.0))

    worker = LifeWorker(cfg)
    rc_holder: dict[str, int] = {}
    def _run() -> None:
        worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]
        rc_holder["rc"] = worker.run_forever()
    t = threading.Thread(target=_run, daemon=True)
    t.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        items = mem.backlog.all()
        if items and items[0].status in ("done", "failed"):
            break
        time.sleep(0.05)

    items = mem.backlog.all()
    assert items and items[0].status == "done"

    worker._stop.set()
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert rc_holder.get("rc") == 0


def test_life_worker_drains_multiple_missions(tmp_path: Path) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path, backend="memory",
        per_mission_cap_usd=10.0, daily_cap_usd=100.0, poll_interval=0.1,
    )
    mem = LifeMemory.open(tmp_path)
    mem.init()
    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]
    t = threading.Thread(target=worker.run_forever, daemon=True)
    t.start()

    for i in range(3):
        mem.backlog.add(BacklogItem.new(
            title=f"task-{i}", objective=f"obj-{i}", max_cost_usd=1.0,
        ))
        time.sleep(0.3)

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if all(it.status == "done" for it in mem.backlog.all()):
            break
        time.sleep(0.1)

    assert all(it.status == "done" for it in mem.backlog.all())

    worker._stop.set()
    t.join(timeout=3.0)
    assert not t.is_alive()


def test_daemon_sink_counts_life_mission_completed() -> None:
    cfg = LifeWorkerConfig(life_dir=Path("/tmp"), backend="memory")
    worker = LifeWorker(cfg)
    sink = _DaemonSink(worker)

    sink.handle_event({"type": "life.mission.completed"})

    assert worker._missions_completed == 1


def test_life_worker_continues_when_telegram_poller_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.1)
    LifeMemory.open(tmp_path).init()

    started = False

    def _boom(_self: object) -> None:
        nonlocal started
        started = True
        raise RuntimeError("telegram poller startup failed")

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {}

    monkeypatch.setenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr("argus_skill.life.telegram_bot.TelegramPoller.start", _boom)
    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()

    assert started is True
    assert rc == 0


def test_format_short_duration() -> None:
    from argus_skill.apps.cli import _format_short_duration
    assert _format_short_duration(0) == "0s"
    assert _format_short_duration(45) == "45s"
    assert _format_short_duration(125) == "2m 5s"
    assert _format_short_duration(3725) == "1h 2m"
    assert _format_short_duration(90061) == "1d 1h"


def test_daemon_pid_path_isolated_from_repl(tmp_path: Path) -> None:
    from argus_skill.daemon.life_worker import _daemon_pid_path
    assert _daemon_pid_path(tmp_path).name == "daemon.pid"
    assert _daemon_pid_path(tmp_path) != tmp_path / "repl.pid"


# ---------------------------------------------------------------------------
# Continuous config (disk-based hot-reload)
# ---------------------------------------------------------------------------

from argus_skill.daemon.life_worker import read_continuous_config, write_continuous_config


def test_read_continuous_config_missing_file(tmp_path: Path) -> None:
    enabled, obj = read_continuous_config(tmp_path)
    assert enabled is False
    assert obj == ""


def test_write_and_read_continuous_config(tmp_path: Path) -> None:
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="optimize everything",
    )
    enabled, obj = read_continuous_config(tmp_path)
    assert enabled is True
    assert obj == "optimize everything"


def test_write_continuous_config_done_reason(tmp_path: Path) -> None:
    import json
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="optimize everything",
        done_reason="planner said done",
    )
    data = json.loads((tmp_path / "continuous.json").read_text())
    assert data["enabled"] is False
    assert data["done_reason"] == "planner said done"
    assert "done_at" in data
    state = read_continuous_state(tmp_path)
    assert state == ContinuousConfigState(
        enabled=False,
        objective="optimize everything",
        done_reason="planner said done",
        done_at=state.done_at,
    )


def test_read_continuous_config_malformed(tmp_path: Path) -> None:
    (tmp_path / "continuous.json").write_text("not json at all")
    enabled, obj = read_continuous_config(tmp_path)
    assert enabled is False
    assert obj == ""


def test_write_continuous_config_atomic(tmp_path: Path) -> None:
    """Temp file should not linger after write."""
    write_continuous_config(tmp_path, enabled=True, objective="test")
    tmp_files = list(tmp_path.glob("continuous.json.*.tmp"))
    assert len(tmp_files) == 0
    assert (tmp_path / "continuous.json").exists()


def test_life_worker_hot_reload_rejects_memory_continuous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    LifeMemory.open(tmp_path).init()
    write_continuous_config(tmp_path, enabled=False, objective="initial objective")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)

    seen: dict[str, Any] = {"runs": 0, "continuous": []}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["runs"] += 1
            if self.config.continuous_config_provider is not None:
                enabled, objective = self.config.continuous_config_provider()
                self.config.continuous = enabled
                if objective:
                    self.config.continuous_objective = objective
            seen["continuous"].append(
                (self.config.continuous, self.config.continuous_objective)
            )
            if seen["runs"] == 1:
                write_continuous_config(
                    tmp_path,
                    enabled=True,
                    objective="manual flip",
                )
                return {"stopped_by": "backlog_empty"}
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(
        LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.01)
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()
    data = json.loads((tmp_path / "continuous.json").read_text())

    assert rc == 0
    assert seen["runs"] == 2
    assert seen["continuous"][0][0] is False
    assert seen["continuous"][1][0] is False
    assert data["enabled"] is False
    assert data["objective"] == "manual flip"
    assert "done_reason" not in data


def test_no_pid_file_means_status_dead(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    assert not pid_path.exists()
    assert read_daemon_status(tmp_path).alive is False
    if pid_path.exists():  # pragma: no cover
        os.unlink(pid_path)
