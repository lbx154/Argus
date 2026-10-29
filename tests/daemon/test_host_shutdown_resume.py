"""Work a host shutdown interrupted resumes when the server starts again.

Replays the hosted trial of 2026-10-29: an operator deploy restarted the tenant
runtime; the project daemon got SIGTERM in the middle of an Engineer round of a
standing campaign ("daemon: received signal 15, requesting stop"), nothing
started it again, and the work silently stopped until a manual Start.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

import argus.daemon.life_worker as life_worker_mod
from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig
from argus.daemon.state import (
    GRACEFUL_STOP_REASON,
    HOST_SHUTDOWN_STOP_REASON,
    DaemonStatus,
    read_continuous_state,
    read_host_shutdown,
    record_host_shutdown,
    request_daemon_control_stop,
    request_daemon_stop,
    stop_daemon,
    write_continuous_config,
)
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import host_resume

BOOT = "2026-10-28T22:44:45+00:00"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", raising=False)


def _sigterm(worker: LifeWorker, monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        life_worker_mod.signal, "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    worker._install_signal_handlers()
    # The boot identity the control watcher binds once status is published.
    worker._control_started_at_iso = BOOT
    handlers[life_worker_mod.signal.SIGTERM](life_worker_mod.signal.SIGTERM, None)


def test_a_sigterm_no_operator_asked_for_is_recorded_as_a_resumable_host_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))
    worker._adopted_continuous_generation = 3

    _sigterm(worker, monkeypatch)

    record = read_host_shutdown(tmp_path)
    assert record is not None
    assert (record.pid, record.started_at_iso, record.campaign_generation) == (os.getpid(), BOOT, 3)
    # The stop itself is unchanged: the mission is interrupted.
    assert worker._stop.is_set() and worker._mission_stop.is_set()


def test_an_operator_stop_is_not_a_host_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))
    worker._adopted_continuous_generation = 3
    # stop_daemon / request_daemon_stop persist this before they signal.
    request_daemon_control_stop(tmp_path, pid=os.getpid(), started_at_iso=BOOT, drain=False)

    _sigterm(worker, monkeypatch)

    assert read_host_shutdown(tmp_path) is None
    assert worker._stop.is_set()


def test_host_shutdown_quiesces_the_campaign_under_its_own_resumable_reason(
    tmp_path: Path,
) -> None:
    write_continuous_config(tmp_path, enabled=True, objective="CVPR paper")
    generation = read_continuous_state(tmp_path).generation
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))
    worker._host_shutdown = True

    worker._quiesce_continuous_on_operator_stop(tmp_path, generation)

    state = read_continuous_state(tmp_path)
    assert not state.enabled
    assert state.done_reason == HOST_SHUTDOWN_STOP_REASON


def test_operator_stop_and_pause_clear_the_host_shutdown_record(tmp_path: Path) -> None:
    for stop in (lambda: stop_daemon(tmp_path), lambda: request_daemon_stop(tmp_path)):
        record_host_shutdown(tmp_path, pid=1, started_at_iso=BOOT, signum=15, campaign_generation=None)
        stop()
        assert read_host_shutdown(tmp_path) is None


# -- server startup ---------------------------------------------------------


def _project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    return life


class _Starter:
    def __init__(self, rc: int = 0) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.rc = rc

    def __call__(self, sid: str, *, global_root=None, resume_continuous=False, reclaim_idle=False):
        self.calls.append((sid, resume_continuous))
        return {"rc": self.rc}


def _interrupted_campaign(root: Path, sid: str) -> Path:
    life = _project(root, sid)
    write_continuous_config(life, enabled=True, objective="CVPR paper")
    generation = read_continuous_state(life).generation
    from argus.daemon.state import compare_and_swap_continuous_config

    assert compare_and_swap_continuous_config(
        life, expected=read_continuous_state(life), enabled=False,
        objective="CVPR paper", done_reason=HOST_SHUTDOWN_STOP_REASON,
    )
    record_host_shutdown(life, pid=17300, started_at_iso=BOOT, signum=15, campaign_generation=generation)
    return life


def test_server_start_resumes_a_campaign_a_host_shutdown_interrupted(tmp_path: Path) -> None:
    life = _interrupted_campaign(tmp_path, "s-dacac579")
    start = _Starter()

    assert host_resume.resume_host_stopped_daemons([tmp_path], start) == ["s-dacac579"]

    assert start.calls == [("s-dacac579", True)]
    assert read_host_shutdown(life) is None


def test_server_start_resumes_interrupted_tasks_without_rearming_a_stopped_campaign(
    tmp_path: Path,
) -> None:
    life = _project(tmp_path, "s-tasks")
    # The operator stopped an earlier campaign; a one-off task was then running.
    write_continuous_config(life, enabled=False, objective="old campaign", done_reason=GRACEFUL_STOP_REASON)
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="one task", objective="do it"))
    assert memory.backlog.claim_next() is not None
    record_host_shutdown(life, pid=1, started_at_iso=BOOT, signum=15, campaign_generation=None)
    start = _Starter()

    host_resume.resume_host_stopped_daemons([tmp_path], start)

    assert start.calls == [("s-tasks", False)]


def test_work_the_operator_paused_since_is_not_resumed(tmp_path: Path) -> None:
    life = _interrupted_campaign(tmp_path, "s-paused")
    # Pause on a dead daemon: the campaign is disabled for the operator's reason.
    write_continuous_config(life, enabled=False, objective="CVPR paper", done_reason="operator pause")
    start = _Starter()

    assert host_resume.resume_host_stopped_daemons([tmp_path], start) == []

    assert start.calls == []
    assert read_host_shutdown(life) is None


def test_a_held_first_task_is_not_started(tmp_path: Path) -> None:
    life = _interrupted_campaign(tmp_path, "s-held")
    from argus.webapi.launch_hold import write_launch_hold

    write_launch_hold(life, {"item": {"id": "a1"}}, None)
    start = _Starter()

    host_resume.resume_host_stopped_daemons([tmp_path], start)

    assert start.calls == []


def test_resume_respects_the_active_daemon_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "1")
    busy = _project(tmp_path, "s-busy")
    first = _interrupted_campaign(tmp_path, "s-first")
    time.sleep(0.01)
    second = _interrupted_campaign(tmp_path, "s-second")
    alive = {busy}
    start = _Starter()

    def status(life_dir: Path) -> DaemonStatus:
        return DaemonStatus(Path(life_dir) in alive, None, None, None, Path(life_dir))

    def starter(sid, **kwargs):
        alive.add(tmp_path / "projects" / sid)
        return start(sid, **kwargs)

    monkeypatch.setattr(host_resume, "read_daemon_status", status)

    assert host_resume.resume_host_stopped_daemons([tmp_path], starter) == []
    # Kept for the operator's Start or the next server start with room.
    assert read_host_shutdown(first) is not None and read_host_shutdown(second) is not None

    alive.clear()
    assert host_resume.resume_host_stopped_daemons([tmp_path], starter) == ["s-second"]
    assert read_host_shutdown(first) is not None


def test_the_web_server_resumes_on_startup(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from argus.daemon import life_worker as daemon_worker
    from argus.webapi import server
    from argus.webapi.daemon_services import DaemonServices

    _interrupted_campaign(tmp_path, "s-dacac579")
    start = _Starter()
    app = server.create_app(
        global_root=tmp_path,
        daemon_services=DaemonServices(read_status=daemon_worker.read_daemon_status, start=start),
    )

    with TestClient(app):
        deadline = time.monotonic() + 10
        while not start.calls and time.monotonic() < deadline:
            time.sleep(0.02)

    assert start.calls == [("s-dacac579", True)]


def test_manager_observation_says_nobody_works_when_no_daemon_runs(tmp_path: Path) -> None:
    from argus.manager.observation import observe_project

    life = _interrupted_campaign(tmp_path, "s-dacac579")
    memory = LifeMemory.open(life)
    memory.backlog.add(BacklogItem.new(title="select a direction", objective="pick one"))
    assert memory.backlog.claim_next() is not None

    daemon = observe_project(life).facts["daemon"]

    assert daemon["alive"] is False
    assert daemon["working"].startswith("no: no daemon is running for this project")
    assert daemon["stopped_by"].startswith("host shutdown")
