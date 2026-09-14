from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import argus.daemon.life_worker as life_worker_mod
import argus.daemon.state as daemon_state
from argus.daemon.config import LifeWorkerConfig
from argus.daemon.life_worker import LifeWorker

_STARTED = "2026-08-13T08:00:00+00:00"


def test_control_request_is_bound_to_pid_and_boot_timestamp(tmp_path: Path) -> None:
    daemon_state.request_daemon_control_stop(
        tmp_path,
        pid=123,
        started_at_iso=_STARTED,
        drain=False,
    )

    assert daemon_state.read_daemon_control_stop(
        tmp_path,
        pid=123,
        started_at_iso=_STARTED,
    ) is not None
    assert daemon_state.read_daemon_control_stop(
        tmp_path,
        pid=124,
        started_at_iso=_STARTED,
    ) is None
    assert daemon_state.read_daemon_control_stop(
        tmp_path,
        pid=123,
        started_at_iso="2026-08-13T08:00:01+00:00",
    ) is None

    daemon_state.clear_daemon_control_stop(
        tmp_path,
        pid=123,
        started_at_iso="2026-08-13T08:00:01+00:00",
    )
    assert (tmp_path / "daemon.stop-request.json").exists()
    daemon_state.clear_daemon_control_stop(
        tmp_path,
        pid=123,
        started_at_iso=_STARTED,
    )
    assert not (tmp_path / "daemon.stop-request.json").exists()


@pytest.mark.parametrize(
    ("drain", "mission_interrupted"),
    [(False, True), (True, False)],
)
def test_worker_consumes_pid_bound_stop_without_console_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drain: bool,
    mission_interrupted: bool,
) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        life_worker_mod.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    monkeypatch.setattr(
        life_worker_mod,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=os.getpid(),
            started_at_iso=_STARTED,
        ),
    )
    daemon_state.request_daemon_control_stop(
        tmp_path,
        pid=os.getpid(),
        started_at_iso=_STARTED,
        drain=drain,
    )
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))

    worker._install_signal_handlers()
    assert worker._stop.wait(timeout=2.0)
    assert worker._operator_stop_requested is True
    assert worker._mission_stop.is_set() is mission_interrupted
    assert signal.SIGTERM in handlers


def test_worker_upgrades_drain_request_to_immediate_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daemon_state.time, "time", lambda: 1000.0)
    monkeypatch.setattr(life_worker_mod.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        life_worker_mod,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=True,
            pid=os.getpid(),
            started_at_iso=_STARTED,
        ),
    )
    daemon_state.request_daemon_control_stop(
        tmp_path,
        pid=os.getpid(),
        started_at_iso=_STARTED,
        drain=True,
    )
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))
    worker._install_signal_handlers()

    assert worker._stop.wait(timeout=2.0)
    assert worker._mission_stop.is_set() is False

    daemon_state.request_daemon_control_stop(
        tmp_path,
        pid=os.getpid(),
        started_at_iso=_STARTED,
        drain=False,
    )

    assert worker._mission_stop.wait(timeout=2.0)


@pytest.mark.skipif(os.name != "nt", reason="Windows signal semantics")
def test_windows_nonblocking_stop_writes_control_without_os_kill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = daemon_state.DaemonStatus(
        alive=True,
        pid=123,
        started_at_iso=_STARTED,
        uptime_seconds=1.0,
        life_dir=tmp_path,
    )
    monkeypatch.setattr(daemon_state, "read_daemon_status", lambda _path: status)
    monkeypatch.setattr(
        daemon_state.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Windows graceful stop must not call os.kill")
        ),
    )

    assert daemon_state.request_daemon_stop(tmp_path) == (True, 123)
    request = daemon_state.read_daemon_control_stop(
        tmp_path,
        pid=123,
        started_at_iso=_STARTED,
    )
    assert request is not None and request.drain is False


@pytest.mark.skipif(os.name != "nt", reason="Windows force-stop implementation")
def test_windows_force_stop_uses_verified_tree_not_console_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = daemon_state.DaemonStatus(
        alive=True,
        pid=123,
        started_at_iso=_STARTED,
        uptime_seconds=1.0,
        life_dir=tmp_path,
    )
    calls: list[int] = []
    monkeypatch.setattr(daemon_state, "read_daemon_status", lambda _path: status)
    monkeypatch.setattr(
        daemon_state.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Windows stop must not broadcast a console signal")
        ),
    )

    def terminate_tree(pid: int, *, identity_check) -> bool:
        assert identity_check() is True
        calls.append(pid)
        return True

    monkeypatch.setattr(daemon_state, "_terminate_windows_process_tree", terminate_tree)

    assert daemon_state.stop_daemon(tmp_path, timeout=0.0, force=True) == 0
    assert calls == [123]


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="native Windows process tree")
def test_native_windows_force_stop_reaps_descendant_tree(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    root = subprocess.Popen([sys.executable, "-c", script])  # noqa: S603
    child_pid = 0
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not child_pid_path.exists():
            time.sleep(0.02)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))

        assert child_pid in daemon_state._descendant_pids(root.pid)
        assert daemon_state._terminate_windows_process_tree(
            root.pid,
            identity_check=lambda: root.poll() is None,
        )
        root.wait(timeout=10.0)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and daemon_state._process_alive(child_pid):
            time.sleep(0.05)
        assert not daemon_state._process_alive(child_pid)
    finally:
        if root.poll() is None:
            root.kill()
        if child_pid and daemon_state._process_alive(child_pid):
            os.kill(child_pid, signal.SIGTERM)
