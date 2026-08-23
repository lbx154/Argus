"""Smoke tests for the 7×24 life worker."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import argus_skill.daemon.life_worker as life_worker_mod
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.daemon.life_worker import (
    ContinuousConfigState,
    DaemonStatus,
    LifeWorker,
    LifeWorkerConfig,
    _config_from_payload,
    _config_payload,
    _DaemonSink,
    _runner_namespace,
    _strip_git_config_injection,
    _worker_runtime_context,
    _workspace_start_error,
    read_continuous_state,
    read_daemon_status,
    resolve_effective_budget,
    stop_daemon,
)
from argus_skill.daemon.state import (
    DAEMON_UPGRADE_REQUEST_FILE,
    _daemon_status_payload,
    daemon_drain_requested,
    request_daemon_drain,
    request_daemon_stop,
)
from argus_skill.life.memory import BacklogItem, LifeMemory

_ENV_VARS_TO_CLEAR = (
    "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG",
    "ARGUS_SKILL_DAEMON_HANDOFF_READY",
    "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN",
    "ARGUS_SKILL_ENGINEER_MODEL",
    "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
    "ARGUS_SKILL_HOME",
    "ARGUS_SKILL_LIFE_BACKEND",
    "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "ARGUS_SKILL_MAX_ROUNDS",
    "ARGUS_SKILL_MODEL",
    "ARGUS_SKILL_PLAN_MODE",
    "ARGUS_SKILL_PLAN_MODEL",
    "ARGUS_SKILL_RESEARCH_PROFILE",
    "ARGUS_SKILL_RESEARCH_PROFILE_PATH",
    "ARGUS_SKILL_REVIEWER_MODEL",
    "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "ARGUS_SKILL_SKILLS_DIR",
    "ARGUS_SKILL_ENABLE_TELEGRAM",
    "ARGUS_SKILL_TELEGRAM_BOT_TOKEN",
    "ARGUS_SKILL_TELEGRAM_CHAT_ID",
    "ARGUS_SKILL_TELEGRAM_USER_ID",
    "ARGUS_SKILL_WORKDIR",
)


@pytest.fixture(autouse=True)
def _clear_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)


def test_max_active_daemons_defaults_to_64(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {},
    )

    assert life_worker_mod._max_active_daemons(LifeWorkerConfig(life_dir=tmp_path)) == 64


def test_runner_namespace_uses_bounded_round_default_with_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    assert _runner_namespace(config).max_rounds == 32

    monkeypatch.setenv("ARGUS_SKILL_MAX_ROUNDS", "7")
    assert _runner_namespace(config).max_rounds == 7


def test_daemon_strict_release_preflight_fails_before_backend_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    monkeypatch.setattr(
        "argus_skill.core.runtime_identity.release_match_preflight_error",
        lambda: "release mismatch",
    )

    result = worker._rf_vault_preflight(SimpleNamespace(cfg=worker.config))

    assert result == 2


def test_max_active_daemons_preserves_env_and_persisted_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {"ARGUS_SKILL_MAX_ACTIVE_DAEMONS": "12"},
    )
    config = LifeWorkerConfig(life_dir=tmp_path)
    assert life_worker_mod._max_active_daemons(config) == 12

    monkeypatch.setenv("ARGUS_SKILL_MAX_ACTIVE_DAEMONS", "7")
    assert life_worker_mod._max_active_daemons(config) == 7


def test_read_daemon_status_returns_not_alive_on_missing_pid(tmp_path: Path) -> None:
    status = read_daemon_status(tmp_path)
    assert isinstance(status, DaemonStatus)
    assert status.alive is False
    assert status.pid is None
    assert status.life_dir == tmp_path


def test_daemon_status_payload_preserves_life_backend(tmp_path: Path) -> None:
    payload = _daemon_status_payload(
        LifeWorkerConfig(life_dir=tmp_path, backend="memory"),
        started_at_iso="2026-07-20T00:00:00+00:00",
    )

    assert payload["life_backend"] == "memory"


def test_drain_signal_does_not_interrupt_active_mission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        life_worker_mod.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))
    request_daemon_drain(tmp_path, pid=os.getpid())
    worker._install_signal_handlers()

    handlers[life_worker_mod.signal.SIGTERM](
        life_worker_mod.signal.SIGTERM,
        None,
    )

    assert worker._stop.is_set()
    assert not worker._mission_stop.is_set()


def test_plain_stop_signal_interrupts_active_mission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(
        life_worker_mod.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path))
    worker._install_signal_handlers()

    handlers[life_worker_mod.signal.SIGTERM](
        life_worker_mod.signal.SIGTERM,
        None,
    )

    assert worker._stop.is_set()
    assert worker._mission_stop.is_set()


def test_memory_runner_leaves_project_structure_to_agent(tmp_path: Path) -> None:
    from argus_skill.apps._runtime_backends import _MemoryRunner

    class Sink:
        def handle_event(self, _event: dict[str, Any]) -> None:
            return None

    runner = _MemoryRunner()
    runner.workdir = tmp_path

    runner.execute(objective="research objective", sink=Sink())

    assert list(tmp_path.iterdir()) == []


def test_read_daemon_status_detects_stale_pid(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("2000000000\n")
    assert read_daemon_status(tmp_path).alive is False


def test_read_daemon_status_rejects_reused_live_pid_without_daemon_lock(
    tmp_path: Path,
) -> None:
    (tmp_path / "daemon.pid").write_text(f"{os.getpid()}\n")

    status = read_daemon_status(tmp_path)

    assert status.alive is False
    assert status.pid is None


def test_read_daemon_status_treats_garbage_pid_file_as_dead(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("not-a-number\n")
    s = read_daemon_status(tmp_path)
    assert s.alive is False and s.pid is None


def test_read_daemon_status_parses_global_budget_cap(tmp_path: Path) -> None:
    from argus_skill.core.daemon_lock import acquire_global_daemon_lock

    pid = os.getpid()
    with acquire_global_daemon_lock(pid_path=tmp_path / "daemon.pid"):
        (tmp_path / "daemon.status.json").write_text(
            json.dumps(
                {
                    "pid": pid,
                    "started_at_iso": "2024-01-01T00:00:00+00:00",
                    "backend": "memory",
                    "life_dir": str(tmp_path),
                    "global_daily_cap_usd": 84.5,
                }
            ),
            encoding="utf-8",
        )
        s = read_daemon_status(tmp_path)
        assert s.alive is True
        assert s.global_daily_cap_usd == 84.5


def test_read_daemon_status_rejects_sidecar_from_different_pid(
    tmp_path: Path,
) -> None:
    from argus_skill.core.daemon_lock import acquire_global_daemon_lock

    with acquire_global_daemon_lock(pid_path=tmp_path / "daemon.pid") as lock:
        (tmp_path / "daemon.status.json").write_text(
            json.dumps(
                {
                    "pid": lock.pid + 1,
                    "backend": "stale-backend",
                    "global_daily_cap_usd": 999,
                }
            ),
            encoding="utf-8",
        )
        status = read_daemon_status(tmp_path)

    assert status.alive is True
    assert status.pid == lock.pid
    assert status.backend is None
    assert status.global_daily_cap_usd is None
    assert "does not match lock pid" in status.status_read_error


def test_resolve_effective_budget_reads_global_config_when_daemon_is_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "global"))
    monkeypatch.delenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", raising=False)
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "config.json").write_text(
        json.dumps({"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "55"}),
        encoding="utf-8",
    )
    status = DaemonStatus(
        alive=False,
        pid=1234,
        started_at_iso=None,
        uptime_seconds=None,
        life_dir=tmp_path,
        backend="memory",
        global_daily_cap_usd=77.0,
    )

    budget = resolve_effective_budget(status)

    assert budget.global_daily_cap_usd == 55.0

    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "999")
    restarted = resolve_effective_budget(status)

    assert restarted.global_daily_cap_usd == 999.0


def test_life_worker_uses_global_config_without_project_budget_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", raising=False)
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"ARGUS_SKILL_GLOBAL_DAILY_CAP_USD": "27"}),
        encoding="utf-8",
    )
    before = config_path.read_bytes()

    config = LifeWorkerConfig(
        life_dir=project,
        global_root=tmp_path,
        backend="memory",
        global_daily_cap_usd=999,
    )
    LifeWorker(config)

    assert config.global_daily_cap_usd == 27
    assert not (project / "budget.json").exists()
    assert config_path.read_bytes() == before


def test_stop_daemon_returns_1_when_no_daemon(tmp_path: Path) -> None:
    assert stop_daemon(tmp_path) == 1


def test_nonblocking_stop_request_revalidates_daemon_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.daemon.state as daemon_state

    started = "2026-08-13T08:00:00+00:00"
    status = SimpleNamespace(
        alive=True,
        pid=4242,
        life_dir=tmp_path,
        started_at_iso=started,
    )
    monkeypatch.setattr(daemon_state, "read_daemon_status", lambda _root: status)
    monkeypatch.setattr(
        daemon_state,
        "_same_daemon_instance_alive",
        lambda _root, _pid, _started: True,
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        daemon_state.os,
        "kill",
        lambda pid, signum: signals.append((pid, signum)),
    )
    (tmp_path / DAEMON_UPGRADE_REQUEST_FILE).write_text("{}", encoding="utf-8")

    requested, pid = request_daemon_stop(tmp_path)

    assert (requested, pid) == (True, 4242)
    assert signals == (
        [] if os.name == "nt" else [(4242, daemon_state.signal.SIGTERM)]
    )
    control = daemon_state.read_daemon_control_stop(
        tmp_path,
        pid=4242,
        started_at_iso=started,
    )
    assert control is not None and control.drain is False
    assert not (tmp_path / DAEMON_UPGRADE_REQUEST_FILE).exists()


def test_nonblocking_stop_request_refuses_stale_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.daemon.state as daemon_state

    status = SimpleNamespace(
        alive=True,
        pid=4242,
        life_dir=tmp_path,
        started_at_iso="2026-08-13T08:00:00+00:00",
    )
    monkeypatch.setattr(daemon_state, "read_daemon_status", lambda _root: status)
    monkeypatch.setattr(
        daemon_state,
        "_same_daemon_instance_alive",
        lambda _root, _pid, _started: False,
    )
    monkeypatch.setattr(
        daemon_state.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("stale daemon identity must not be signalled")
        ),
    )

    assert request_daemon_stop(tmp_path) == (False, None)


def test_explicit_stop_cancels_pending_daemon_upgrade(tmp_path: Path) -> None:
    request = tmp_path / DAEMON_UPGRADE_REQUEST_FILE
    request.write_text('{"schema_version": 1}\\n', encoding="utf-8")

    assert stop_daemon(tmp_path) == 1
    assert not request.exists()


def test_clean_spawn_execs_helper_without_inheriting_parent_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    shadow = workdir / "argus_skill"
    shadow.mkdir()
    (shadow / "__init__.py").write_text(
        "raise RuntimeError('workspace package shadow was imported')\n",
        encoding="utf-8",
    )
    config = LifeWorkerConfig(
        life_dir=tmp_path / "life",
        global_root=tmp_path,
        project_workdir=workdir,
        continuous=True,
        continuous_objective="continue research",
        resume_continuous=True,
    )
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(life_worker_mod.subprocess, "run", fake_run)

    assert life_worker_mod.spawn_detached_daemon_clean(config, quiet=True) == 0
    assert captured["command"] == [
        life_worker_mod.sys.executable,
        "-m",
        "argus_skill.daemon.spawn_helper",
    ]
    assert captured["close_fds"] is True
    import_root = Path(life_worker_mod.__file__).resolve().parents[2]
    assert captured["cwd"] == str(import_root)
    assert captured["env"]["PYTHONSAFEPATH"] == "1"
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(import_root)
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    if os.name == "nt":
        assert captured["timeout"] > 180.0
    else:
        assert captured["timeout"] == 15.0
    payload = json.loads(captured["input"])
    assert payload["life_dir"] == str(config.life_dir)
    assert payload["continuous_objective"] == "continue research"

    # Exercise the captured clean-import context with a fresh interpreter. The
    # workspace package would raise immediately if cwd/PYTHONPATH were unsafe.
    import subprocess

    probe = subprocess.Popen(  # noqa: S603
        [
            life_worker_mod.sys.executable,
            "-c",
            "import argus_skill; print(argus_skill.__file__)",
        ],
        cwd=captured["cwd"],
        env=captured["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = probe.communicate(timeout=10)
    assert probe.returncode == 0, stderr
    assert str(shadow) not in stdout


def test_clean_spawn_preserves_helper_stderr_for_webapi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    config = LifeWorkerConfig(
        life_dir=tmp_path / "life",
        global_root=tmp_path,
        project_workdir=workdir,
    )
    monkeypatch.setattr(
        life_worker_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Traceback: UnicodeEncodeError in daemon bootstrap",
        ),
    )

    with caplog.at_level(logging.ERROR):
        rc = life_worker_mod.spawn_detached_daemon_clean(config, quiet=True)

    assert rc == 1
    assert config.last_spawn_error == "Traceback: UnicodeEncodeError in daemon bootstrap"
    assert config.last_spawn_error in caplog.text


def test_clean_spawn_rejects_missing_interpreter_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    config = LifeWorkerConfig(
        life_dir=tmp_path / "life",
        global_root=tmp_path,
        project_workdir=workdir,
    )
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("preflight must reject a missing interpreter")

    monkeypatch.setattr(life_worker_mod.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(life_worker_mod.subprocess, "run", fail_if_called)

    assert life_worker_mod.spawn_detached_daemon_clean(config, quiet=True) == 2
    assert called is False
    assert "Python interpreter is unavailable" in config.last_spawn_error


def test_clean_spawn_rejects_missing_workdir_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LifeWorkerConfig(
        life_dir=tmp_path / "life",
        global_root=tmp_path,
        project_workdir=tmp_path / "missing",
    )
    monkeypatch.setattr(
        life_worker_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight must reject a missing workdir")
        ),
    )

    assert life_worker_mod.spawn_detached_daemon_clean(config, quiet=True) == 3
    assert "configured workdir is unavailable" in config.last_spawn_error


def test_stop_daemon_does_not_sigkill_after_pid_identity_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.daemon import state as daemon_state

    statuses = iter(
        [
            DaemonStatus(True, 123, "2026-08-13T08:00:00+00:00", None, tmp_path),
            DaemonStatus(True, 123, "2026-08-13T08:00:00+00:00", None, tmp_path),
            DaemonStatus(False, None, None, None, tmp_path),
        ]
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(daemon_state, "read_daemon_status", lambda _path: next(statuses))
    monkeypatch.setattr(
        daemon_state.os,
        "kill",
        lambda pid, sig: signals.append((pid, sig)),
    )

    assert life_worker_mod.stop_daemon(tmp_path, timeout=1.0, force=True) == 0
    assert signals == (
        [] if os.name == "nt" else [(123, daemon_state.signal.SIGTERM)]
    )


def _spawn_fake_daemon(tmp_path: Path, pre_ready: str, post_ready: str) -> int:
    """Spawn a DETACHED fake daemon (double-fork, reparented to init) and return
    its pid.

    Detaching mirrors the real daemon (spawn_detached_daemon) and — crucially —
    means the exited process is reaped by init, not left a zombie under the test
    process. A zombie still answers ``os.kill(pid, 0)``, which would make
    stop_daemon's liveness check wrongly report 'still alive'. ``pre_ready``
    installs SIGTERM handling before the ready-marker is touched.
    """
    if not hasattr(os, "fork"):
        pytest.skip("detached daemon fixture requires POSIX os.fork")
    pytest.importorskip("fcntl", reason="detached daemon fixture requires fcntl")

    import subprocess
    import sys

    ready = tmp_path / "fake_daemon.ready"
    pid_path = life_worker_mod._daemon_pid_path(tmp_path)
    script = (
        "import fcntl, signal, time, sys, os, pathlib\n"
        "if os.fork() > 0:\n"
        "    os._exit(0)\n"  # immediate child exits -> grandchild reparented to init
        "os.setsid()\n"
        + pre_ready
        + f"_pid_path = {str(pid_path)!r}\n"
        + "_pid_fd = os.open(_pid_path, os.O_CREAT | os.O_RDWR, 0o600)\n"
        + "fcntl.flock(_pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        + "os.ftruncate(_pid_fd, 0)\n"
        + "os.write(_pid_fd, str(os.getpid()).encode())\n"
        + f"pathlib.Path({str(ready)!r}).write_text('1')\n"
        + post_ready
    )
    proc = subprocess.Popen([sys.executable, "-c", script])  # noqa: S603
    proc.wait(timeout=5)  # reap the immediate child; the grandchild runs detached
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ready.exists() and life_worker_mod.read_daemon_status(tmp_path).alive:
            break
        time.sleep(0.02)
    pid = life_worker_mod.read_daemon_status(tmp_path).pid
    assert pid is not None
    return pid


def _reap_fake_daemon(pid: int) -> None:
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


@pytest.mark.integration
def test_stop_daemon_drain_quiesces_continuous_and_waits_for_clean_exit(
    tmp_path: Path,
) -> None:
    # Fake daemon: SIGTERM sets a stop flag; the main loop notices, finishes its
    # 'mission' (a short delay) then exits cleanly — modelling the supervisor
    # finishing the current mission before the process leaves. Drain must wait
    # that boundary out, not SIGKILL.
    pid = _spawn_fake_daemon(
        tmp_path,
        pre_ready=(
            "_stop = []\ndef _h(*a):\n    _stop.append(1)\nsignal.signal(signal.SIGTERM, _h)\n"
        ),
        post_ready=("while not _stop:\n    time.sleep(0.05)\ntime.sleep(0.8)\nos._exit(0)\n"),
    )
    try:
        life_worker_mod.write_continuous_config(tmp_path, enabled=True, objective="keep going")
        rc = life_worker_mod.stop_daemon(tmp_path, drain=True, drain_timeout=15.0)
        assert rc == 0
        # Drain quiesced continuous mode (no NEW mission), preserving the objective.
        assert life_worker_mod.read_continuous_config(tmp_path) == (False, "keep going")
        assert not life_worker_mod._process_alive(pid)  # really exited
        assert not daemon_drain_requested(tmp_path, pid=pid)
    finally:
        _reap_fake_daemon(pid)


@pytest.mark.integration
def test_stop_daemon_force_sigkills_a_stuck_daemon(tmp_path: Path) -> None:
    # A daemon that ignores SIGTERM (mid-mission, never reaches a boundary): plain
    # stop times out (rc=2), --force escalates to SIGKILL (rc=0).
    pid = _spawn_fake_daemon(
        tmp_path,
        pre_ready="signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        post_ready="time.sleep(60)\n",
    )
    try:
        assert life_worker_mod.stop_daemon(tmp_path, timeout=1.0) == 2
        assert life_worker_mod._process_alive(pid)  # SIGTERM ignored, still alive
        assert life_worker_mod.stop_daemon(tmp_path, timeout=1.0, force=True) == 0
        time.sleep(0.3)
        assert not life_worker_mod._process_alive(pid)  # SIGKILLed
    finally:
        _reap_fake_daemon(pid)


@pytest.mark.integration
def test_stop_daemon_force_kills_detached_descendants(tmp_path: Path) -> None:
    child_pid_path = tmp_path / "fake_daemon.child.pid"
    pid = _spawn_fake_daemon(
        tmp_path,
        pre_ready="signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        post_ready=(
            "child = os.fork()\n"
            "if child == 0:\n"
            "    os.setsid()\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"    pathlib.Path({str(child_pid_path)!r}).write_text(str(os.getpid()))\n"
            "    time.sleep(60)\n"
            "    os._exit(0)\n"
            "time.sleep(60)\n"
        ),
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not child_pid_path.exists():
        time.sleep(0.02)
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    try:
        assert life_worker_mod._process_alive(child_pid)
        assert (
            life_worker_mod.stop_daemon(
                tmp_path,
                timeout=0.1,
                force=True,
            )
            == 0
        )
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and (
            life_worker_mod._process_alive(pid) or life_worker_mod._process_alive(child_pid)
        ):
            time.sleep(0.05)
        assert not life_worker_mod._process_alive(pid)
        assert not life_worker_mod._process_alive(child_pid)
    finally:
        _reap_fake_daemon(pid)
        _reap_fake_daemon(child_pid)


@pytest.mark.integration
def test_stop_daemon_force_kills_teammate_process_group(tmp_path: Path) -> None:
    teammate_pid_path = tmp_path / "teammate.pid"
    provider_pid_path = tmp_path / "provider.pid"
    teammate_script = (
        "import os, pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "provider = os.fork()\n"
        "if provider == 0:\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"    pathlib.Path({str(provider_pid_path)!r}).write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        "time.sleep(60)\n"
    )
    pid = _spawn_fake_daemon(
        tmp_path,
        pre_ready="signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        post_ready=(
            "child = os.fork()\n"
            "if child == 0:\n"
            "    os.setsid()\n"
            f"    pathlib.Path({str(teammate_pid_path)!r}).write_text(str(os.getpid()))\n"
            "    os.execv(sys.executable, [\n"
            "        sys.executable,\n"
            "        '-c',\n"
            f"        {teammate_script!r},\n"
            "        'argus_skill.team.teammate_entry',\n"
            "        '--root',\n"
            f"        {str(tmp_path / 'team')!r},\n"
            "        '--member-id',\n"
            "        'w1',\n"
            "    ])\n"
            "time.sleep(60)\n"
        ),
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not (
        teammate_pid_path.exists() and provider_pid_path.exists()
    ):
        time.sleep(0.02)
    teammate_pid = int(teammate_pid_path.read_text(encoding="utf-8"))
    provider_pid = int(provider_pid_path.read_text(encoding="utf-8"))
    try:
        assert life_worker_mod._process_alive(teammate_pid)
        assert life_worker_mod._process_alive(provider_pid)
        assert life_worker_mod.stop_daemon(
            tmp_path,
            timeout=0.1,
            force=True,
        ) == 0
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and any(
            life_worker_mod._process_alive(candidate)
            for candidate in (pid, teammate_pid, provider_pid)
        ):
            time.sleep(0.05)
        assert not life_worker_mod._process_alive(pid)
        assert not life_worker_mod._process_alive(teammate_pid)
        assert not life_worker_mod._process_alive(provider_pid)
    finally:
        _reap_fake_daemon(pid)
        _reap_fake_daemon(teammate_pid)
        _reap_fake_daemon(provider_pid)


@pytest.mark.integration
def test_force_drain_clears_pid_bound_request(tmp_path: Path) -> None:
    pid = _spawn_fake_daemon(
        tmp_path,
        pre_ready="signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        post_ready="time.sleep(60)\n",
    )
    try:
        assert (
            life_worker_mod.stop_daemon(
                tmp_path,
                drain=True,
                drain_timeout=0.1,
                force=True,
            )
            == 0
        )
        assert not daemon_drain_requested(tmp_path, pid=pid)
    finally:
        _reap_fake_daemon(pid)


@pytest.mark.integration
def test_draining_daemon_remains_alive_if_pid_path_disappears(tmp_path: Path) -> None:
    pid = _spawn_fake_daemon(
        tmp_path,
        pre_ready="signal.signal(signal.SIGTERM, signal.SIG_IGN)\n",
        post_ready="time.sleep(60)\n",
    )
    try:
        assert (
            life_worker_mod.stop_daemon(
                tmp_path,
                drain=True,
                drain_timeout=0.1,
            )
            == 2
        )
        life_worker_mod._daemon_pid_path(tmp_path).unlink()

        status = life_worker_mod.read_daemon_status(tmp_path)

        assert status.alive is True
        assert status.pid == pid
    finally:
        _reap_fake_daemon(pid)


@pytest.mark.integration
def test_life_worker_drains_successive_missions_and_stops_on_signal(
    tmp_path: Path,
) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        global_daily_cap_usd=0.0,
        poll_interval=0.1,
    )
    mem = LifeMemory.open(tmp_path)
    mem.init()
    first = BacklogItem.new(title="first", objective="say first")
    mem.backlog.add(first)

    worker = LifeWorker(cfg)
    rc_holder: dict[str, int] = {}

    def _run() -> None:
        worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]
        rc_holder["rc"] = worker.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    _wait_for_backlog_item_status(mem, first.id, "done", timeout=30.0)

    second = BacklogItem.new(
        title="second",
        objective="say second",
    )
    mem.backlog.add(second)
    _wait_for_backlog_item_status(mem, second.id, "done", timeout=30.0)

    worker._stop.set()
    _wait_for_thread_stop(t, timeout=10.0)
    assert rc_holder == {"rc": 0}


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
    monkeypatch.setenv("ARGUS_SKILL_ENABLE_TELEGRAM", "1")
    monkeypatch.setattr("argus_skill.life.telegram_bot.TelegramPoller.start", _boom)
    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()

    assert started is True
    assert rc == 0


def test_life_worker_exports_custom_global_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_root = tmp_path / "custom-home"
    project_root = tmp_path / "project"
    project_root.mkdir()

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {}

    monkeypatch.delenv("ARGUS_SKILL_HOME", raising=False)
    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=global_root / "projects" / "demo",
            global_root=global_root,
            project_fingerprint="demo",
            project_workdir=project_root,
            backend="memory",
            poll_interval=0.01,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert os.environ["ARGUS_SKILL_HOME"] == str(global_root.resolve())


def test_life_worker_does_not_start_telegram_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.1)
    LifeMemory.open(tmp_path).init()

    started = False

    def _start(_self: object) -> None:
        nonlocal started
        started = True

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {}

    monkeypatch.setenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", "123")
    monkeypatch.delenv("ARGUS_SKILL_ENABLE_TELEGRAM", raising=False)
    monkeypatch.setattr("argus_skill.life.telegram_bot.TelegramPoller.start", _start)
    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()

    assert started is False
    assert rc == 0


def test_life_worker_separates_boundary_stop_from_mission_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "1")
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness.check_backend_readiness",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True),
    )
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="codex", poll_interval=0.1)
    LifeMemory.open(tmp_path).init()
    captured: dict[str, Any] = {}

    def fake_build_life_runner(ns: Any) -> object:
        captured["stop_event"] = getattr(ns, "stop_event", None)
        return object()

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]
            captured["supervisor_stop_event"] = self.config.stop_event

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {}

    monkeypatch.setattr(
        "argus_skill.apps._runtime.build_life_runner",
        fake_build_life_runner,
    )
    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()

    assert captured["stop_event"] is worker._mission_stop
    assert captured["supervisor_stop_event"] is worker._stop
    assert rc == 0


def test_format_short_duration() -> None:
    from argus_skill.apps.cli._core import _format_short_duration

    assert _format_short_duration(0) == "0s"
    assert _format_short_duration(45) == "45s"
    assert _format_short_duration(125) == "2m 5s"
    assert _format_short_duration(3725) == "1h 2m"
    assert _format_short_duration(90061) == "1d 1h"


@pytest.mark.parametrize(
    ("skills_env", "expected"),
    [
        (None, "root"),
        ("custom-skills", "custom-skills"),
    ],
)
def test_runner_namespace_uses_global_skills_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    skills_env: str | None,
    expected: str,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "root"))
    monkeypatch.delenv("ARGUS_SKILL_SKILLS_DIR", raising=False)
    if skills_env is not None:
        monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / skills_env))

    ns = _runner_namespace(
        LifeWorkerConfig(
            life_dir=tmp_path / "life",
            backend="memory",
            project_workdir=tmp_path / "repo",
        )
    )

    expected_path = tmp_path / expected / "skills" if skills_env is None else tmp_path / expected
    assert ns.skills_dir == str(expected_path)
    assert ns.workdir == str(tmp_path / "repo")
    assert ns.global_root == str(tmp_path / "root")
    assert ns.engineer_reasoning_effort == "xhigh"
    assert ns.reviewer_reasoning_effort == "high"


def test_workspace_start_rejects_another_live_session_on_same_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    target_life = root / "projects" / "s-target"
    owner_life = root / "projects" / "s-owner"
    workdir = tmp_path / "repo"
    target_life.mkdir(parents=True)
    owner_life.mkdir(parents=True)
    workdir.mkdir()
    write_session_meta(
        root,
        SessionMeta(id="s-target", cwd=str(target_life), workdir=str(workdir)),
    )
    write_session_meta(
        root,
        SessionMeta(id="s-owner", cwd=str(owner_life), workdir=str(workdir)),
    )

    def status(path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            alive=Path(path) == owner_life,
            pid=321,
            project_workdir="",
        )

    monkeypatch.setattr(life_worker_mod, "read_daemon_status", status)
    error = _workspace_start_error(
        LifeWorkerConfig(
            life_dir=target_life,
            global_root=root,
            project_workdir=workdir,
            project_fingerprint="s-target",
        )
    )

    assert "already owned by active session s-owner" in error


def test_workspace_start_rejects_file_instead_of_directory(tmp_path: Path) -> None:
    root = tmp_path / "state"
    life_dir = root / "projects" / "s-target"
    workdir = tmp_path / "not-a-directory.txt"
    life_dir.mkdir(parents=True)
    workdir.write_text("not a workspace", encoding="utf-8")

    error = _workspace_start_error(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=root,
            project_workdir=workdir,
            project_fingerprint="s-target",
        )
    )

    assert "configured workdir is not a directory" in error


def test_workspace_start_rejects_another_session_on_adopted_child_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from argus_skill.core.campaign_workdir import adopt_campaign_workdir

    root = tmp_path / "state"
    target_life = root / "projects" / "s-target"
    owner_life = root / "projects" / "s-owner"
    workspace = tmp_path / "workspace"
    child = workspace / "target-repo"
    target_life.mkdir(parents=True)
    owner_life.mkdir(parents=True)
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(child)], check=True)
    write_session_meta(
        root,
        SessionMeta(id="s-target", cwd=str(target_life), workdir=str(child)),
    )
    write_session_meta(
        root,
        SessionMeta(id="s-owner", cwd=str(owner_life), workdir=str(workspace)),
    )
    adopt_campaign_workdir(
        state_root=owner_life,
        base_root=workspace,
        current_root=workspace,
        requested="target-repo",
    )

    def status(path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            alive=Path(path) == owner_life,
            pid=654,
            project_workdir=str(workspace) if Path(path) == owner_life else "",
        )

    monkeypatch.setattr(life_worker_mod, "read_daemon_status", status)
    error = _workspace_start_error(
        LifeWorkerConfig(
            life_dir=target_life,
            global_root=root,
            project_workdir=child,
            project_fingerprint="s-target",
        )
    )

    assert "already owned by active session s-owner" in error


def test_workspace_start_rejects_stale_config_after_workdir_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    life_dir = root / "projects" / "s-target"
    old = tmp_path / "old"
    new = tmp_path / "new"
    life_dir.mkdir(parents=True)
    old.mkdir()
    new.mkdir()
    write_session_meta(
        root,
        SessionMeta(id="s-target", cwd=str(life_dir), workdir=str(new)),
    )

    error = _workspace_start_error(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=root,
            project_workdir=old,
            project_fingerprint="s-target",
        )
    )

    assert "workdir changed during daemon startup" in error


def test_workspace_start_rejects_legacy_daemon_workdir_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    life_dir = root / "projects" / "legacy"
    old = tmp_path / "old"
    life_dir.mkdir(parents=True)
    old.mkdir()
    monkeypatch.setattr(
        life_worker_mod,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=False,
            pid=None,
            project_workdir=str(old),
        ),
    )

    error = _workspace_start_error(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=root,
            project_workdir=life_dir,
            project_fingerprint="legacy",
        )
    )

    assert "legacy session workdir changed" in error
    assert str(old) in error


def test_workspace_start_rejects_unavailable_legacy_daemon_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "state"
    life_dir = root / "projects" / "legacy"
    life_dir.mkdir(parents=True)
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        life_worker_mod,
        "read_daemon_status",
        lambda _path: SimpleNamespace(
            alive=False,
            pid=None,
            project_workdir=str(missing),
        ),
    )

    error = _workspace_start_error(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=root,
            project_workdir=life_dir,
            project_fingerprint="legacy",
        )
    )

    assert "previous workdir is unavailable" in error
    assert str(missing) in error


def test_handoff_config_payload_round_trips(tmp_path: Path) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path / "project",
        global_root=tmp_path / "global",
        project_workdir=tmp_path / "repo",
        project_fingerprint="abc123",
        project_label="demo",
        backend="codex",
        engineer_model="eng",
        reviewer_model="rev",
        global_daily_cap_usd=9.5,
        poll_interval=0.25,
        log_path=tmp_path / "daemon.log",
        continuous=True,
        continuous_objective="keep improving",
        resume_continuous=True,
        continuous_open_ended=False,
    )

    restored = _config_from_payload(_config_payload(cfg))

    assert restored == cfg
    assert restored.resume_continuous is True
    assert restored.continuous_open_ended is False


def test_handoff_config_preserves_native_backend_model_sentinels(
    tmp_path: Path,
) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path / "project",
        backend="claude",
        engineer_model="",
        reviewer_model="",
    )

    restored = _config_from_payload(_config_payload(cfg))

    assert restored.engineer_model == ""
    assert restored.reviewer_model == ""


def test_handoff_config_preserves_explicit_zero_global_budget_cap(tmp_path: Path) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path / "project",
        global_daily_cap_usd=0.0,
    )

    restored = _config_from_payload(_config_payload(cfg))

    assert restored.global_daily_cap_usd == 0.0


def test_active_daemon_count_resolves_default_global_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(life_worker_mod.core_paths, "global_root", lambda: tmp_path)
    (tmp_path / "projects").mkdir()

    assert (
        life_worker_mod._active_daemon_count(
            LifeWorkerConfig(life_dir=tmp_path / "project", global_root=None)
        )
        == 0
    )


def test_handoff_lock_wait_retries_until_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    expected_lock = object()

    def _fake_acquire(*, pid_path: Path) -> object:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise life_worker_mod.DaemonAlreadyRunning(123, pid_path)
        return expected_lock

    monkeypatch.setattr(
        life_worker_mod,
        "acquire_global_daemon_lock",
        _fake_acquire,
    )
    monkeypatch.setattr(life_worker_mod.time, "sleep", lambda _seconds: None)

    lock = life_worker_mod._acquire_daemon_lock_with_timeout(
        tmp_path / "daemon.pid",
        timeout=1.0,
    )

    assert lock is expected_lock
    assert attempts == 3


def test_life_worker_post_mission_hook_canaries_reviewed_self_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: dict[str, object] = {}
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    framework = tmp_path / "framework"
    framework.mkdir()

    class _Maintenance:
        def prepare_reviewed_change(self, _outcome):
            return candidate

        def mark_handoff_failed(self, _reason):
            raise AssertionError("reviewed candidate should reach standby")

    def _fake_spawn(config: LifeWorkerConfig, **kwargs: object) -> bool:
        spawned["config"] = config
        spawned.update(kwargs)
        return True

    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", _fake_spawn)
    monkeypatch.setattr(
        "argus_skill.core.runtime_identity.source_root",
        lambda: framework,
    )

    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")
    worker = LifeWorker(cfg)
    worker._self_maintenance = _Maintenance()

    assert worker._post_mission_hook({"status": "done"}) == "daemon_handoff"
    assert worker._stop.is_set()
    assert spawned["config"] is cfg
    assert spawned["candidate_source_root"] == candidate
    assert spawned["rollback_source_root"] == framework
    assert "self-maintenance" in str(spawned["reason"])


def test_worker_reconciles_prepared_item_before_backlog_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    life_dir = tmp_path / "life"
    memory = LifeMemory.open(life_dir)
    memory.init()
    worktree = life_dir / "self-maintenance" / "repairs" / "incident123"
    worktree.mkdir(parents=True)
    item = memory.backlog.add(BacklogItem.new(
        title="repair",
        objective="repair",
        tags=["framework_maintenance"],
        execution_workdir=str(worktree),
    ))
    state_path = life_dir / "self-maintenance" / "state.json"
    state_path.write_text(
        json.dumps({
            "phase": "preparing",
            "repair_mode": "packaged_clone",
            "incident_id": "incident123",
            "worktree": str(worktree),
        }),
        encoding="utf-8",
    )
    runtime = tmp_path / "site-packages" / "argus_skill"
    runtime.mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        "argus_skill.core.runtime_identity.source_root",
        lambda: runtime,
    )
    monkeypatch.setattr(
        "argus_skill.core.runtime_identity.source_revision",
        lambda: "",
    )
    config = LifeWorkerConfig(
        life_dir=life_dir,
        backend="codex",
        project_workdir=project,
        poll_interval=0.0,
    )
    worker = LifeWorker(config)
    observed: list[tuple[str, str]] = []

    class _Budget:
        @staticmethod
        def can_start(**_kwargs):
            return True, ""

    class _Supervisor:
        config = SimpleNamespace(budget=_Budget())
        _missions_started = 0
        _planning_cycles = 0

        def run(self):
            state = worker._self_maintenance._state()
            observed.append((state["phase"], state["active_item_id"]))
            memory.backlog.mark_done(item.id)
            worker._stop.set()
            return {"stopped_by": ""}

    supervisor = _Supervisor()
    rf_state = SimpleNamespace(
        runtime_root=life_dir,
        cfg=config,
        runner=SimpleNamespace(manager=SimpleNamespace()),
        mem=memory,
        sink=SimpleNamespace(handle_event=lambda _event: None),
        daemon_sink=SimpleNamespace(self_maintenance=None),
        sup=supervisor,
    )

    assert worker._rf_init_self_maintenance(rf_state) is None
    worker._rf_main_loop(rf_state)

    assert observed == [("queued", item.id)]
    assert memory.backlog.all()[0].status == "done"


def test_life_worker_post_mission_hook_closes_canary_during_continuous_drain(
    tmp_path: Path,
) -> None:
    summaries: list[dict[str, object]] = []
    outcome = {"status": "done", "success": True, "item_id": "mission-1"}

    class _Maintenance:
        def publish_after_canary(self, *, summary):
            summaries.append(summary)
            return "reviewed-commit"

        @staticmethod
        def prepare_reviewed_change(_outcome):
            return None

    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    worker._self_maintenance = _Maintenance()

    assert worker._post_mission_hook(outcome) == ""
    assert summaries == [{
        "stopped_by": "",
        "planning_cycles": 0,
        "results": [outcome],
    }]
    assert not worker._stop.is_set()


def test_life_worker_post_mission_hook_rolls_back_failed_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rollback = tmp_path / "rollback"
    rollback.mkdir()
    spawned: dict[str, object] = {}

    class _Maintenance:
        @staticmethod
        def publish_after_canary(*, summary):  # noqa: ARG004
            return f"rollback:{rollback}"

        @staticmethod
        def prepare_reviewed_change(_outcome):
            raise AssertionError("failed canary must roll back before preparing work")

        @staticmethod
        def mark_handoff_failed(_reason):
            raise AssertionError("rollback should reach standby")

    def _fake_spawn(config: LifeWorkerConfig, **kwargs: object) -> bool:
        spawned["config"] = config
        spawned.update(kwargs)
        return True

    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", _fake_spawn)
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")
    worker = LifeWorker(cfg)
    worker._self_maintenance = _Maintenance()

    assert worker._post_mission_hook({"status": "failed", "success": False}) == (
        "daemon_handoff"
    )
    assert worker._stop.is_set()
    assert spawned["config"] is cfg
    assert spawned["candidate_source_root"] == rollback
    assert "restore prior runtime" in str(spawned["reason"])


def test_life_worker_post_mission_hook_noops_without_reviewed_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_called = False

    def _fake_spawn(*_args: object, **_kwargs: object) -> bool:
        nonlocal spawn_called
        spawn_called = True
        return True

    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", _fake_spawn)

    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    worker._self_maintenance = None

    assert worker._post_mission_hook({"status": "done"}) == ""
    assert not worker._stop.is_set()
    assert spawn_called is False


def test_worker_runtime_context_includes_research_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "custom-public-profile")
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    cfg = LifeWorkerConfig(
        life_dir=tmp_path,
        backend="codex",
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4-mini",
        global_daily_cap_usd=20.0,
    )

    context = _worker_runtime_context(cfg)

    assert "Runtime info" in context
    assert "Engineer model: gpt-5.4-mini" in context
    assert "profile_name: custom-public-profile" in context
    assert "profile_sha256:" not in context


def test_worker_runtime_context_omits_research_profile_for_bounded_vertical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "custom-public-profile")
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "no_special_prompts"))
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    assert _worker_runtime_context(cfg, paper_mission=False) == ""


def test_worker_runtime_context_empty_without_research_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    # Isolate operator special prompts so the host's directives don't leak in.
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "no_special_prompts"))
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    assert _worker_runtime_context(cfg) == ""


def test_worker_runtime_context_surfaces_operator_special_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator directives lead the runtime context, even with no profile."""
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    sp = tmp_path / "special"
    sp.mkdir()
    (sp / "10-gpu.md").write_text("Free the keep-alive before training.", encoding="utf-8")
    (sp / "10-gpu.md").chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp))
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    context = _worker_runtime_context(cfg)
    assert "Operator Directives" in context
    assert "Free the keep-alive before training." in context


def test_handoff_child_publishes_standby_then_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path / "life", backend="memory")
    cfg.life_dir.mkdir(parents=True)
    config_path = tmp_path / "handoff-config.json"
    ready_path = tmp_path / "handoff-ready.json"
    config_path.write_text(
        json.dumps({"config": _config_payload(cfg)}),
        encoding="utf-8",
    )
    released = False

    class FakeLock:
        def release(self) -> None:
            nonlocal released
            released = True

    def _fake_run_forever(self: LifeWorker) -> int:
        assert self.config == cfg
        return 0

    def _fake_acquire(_pid_path: Path, timeout: float) -> FakeLock:
        assert timeout == 60.0
        data = json.loads(ready_path.read_text(encoding="utf-8"))
        assert data["state"] == "standby"
        assert data["token"] == "token-1"
        return FakeLock()

    monkeypatch.setenv(
        life_worker_mod._HANDOFF_CONFIG_ENV,
        str(config_path),
    )
    monkeypatch.setenv(life_worker_mod._HANDOFF_READY_ENV, str(ready_path))
    monkeypatch.setenv(life_worker_mod._HANDOFF_TOKEN_ENV, "token-1")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "0")
    monkeypatch.setattr(
        life_worker_mod,
        "_acquire_daemon_lock_with_timeout",
        _fake_acquire,
    )
    monkeypatch.setattr(LifeWorker, "run_forever", _fake_run_forever)

    rc = life_worker_mod.run_handoff_child()

    assert rc == 0
    assert released is True
    assert not ready_path.exists()
    assert not config_path.exists()


@pytest.mark.parametrize(
    "phase",
    ["canary_running", "local_active", "pr_open", "adopted"],
)
def test_failed_self_maintenance_handoff_marks_failed_before_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    import argus_skill.daemon.handoff as handoff_mod

    cfg = LifeWorkerConfig(life_dir=tmp_path / "life", backend="memory")
    cfg.life_dir.mkdir(parents=True)
    state_path = cfg.life_dir / "self-maintenance" / "state.json"
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps({"phase": phase}),
        encoding="utf-8",
    )
    rollback = tmp_path / "prior-source"
    rollback.mkdir()
    config_path = tmp_path / "handoff-config.json"
    ready_path = tmp_path / "handoff-ready.json"
    config_path.write_text(
        json.dumps({"config": _config_payload(cfg)}),
        encoding="utf-8",
    )
    spawned: dict[str, object] = {}

    class FakeLock:
        def release(self) -> None:
            return

    def fake_spawn(config, **kwargs):
        spawned["config"] = config
        spawned.update(kwargs)
        return True

    monkeypatch.setenv(life_worker_mod._HANDOFF_CONFIG_ENV, str(config_path))
    monkeypatch.setenv(life_worker_mod._HANDOFF_READY_ENV, str(ready_path))
    monkeypatch.setenv(life_worker_mod._HANDOFF_TOKEN_ENV, "token-failed")
    monkeypatch.setenv(
        handoff_mod._HANDOFF_ROLLBACK_SOURCE_ENV,
        str(rollback),
    )
    monkeypatch.setattr(
        life_worker_mod,
        "_acquire_daemon_lock_with_timeout",
        lambda *_args: FakeLock(),
    )
    monkeypatch.setattr(LifeWorker, "run_forever", lambda _self: 2)
    monkeypatch.setattr(handoff_mod, "_spawn_handoff_candidate", fake_spawn)

    assert life_worker_mod.run_handoff_child() == 2
    assert json.loads(state_path.read_text())["phase"] == "canary_failed"
    assert spawned["candidate_source_root"] == rollback


def test_daemon_pid_path(tmp_path: Path) -> None:
    from argus_skill.daemon.life_worker import _daemon_pid_path

    assert _daemon_pid_path(tmp_path).name == "daemon.pid"


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


def test_legacy_continuous_config_defaults_to_open_ended(tmp_path: Path) -> None:
    (tmp_path / "continuous.json").write_text(
        json.dumps({"enabled": True, "objective": "legacy campaign"}),
        encoding="utf-8",
    )

    assert read_continuous_state(tmp_path).open_ended is True


def test_bounded_continuous_config_preserves_lifetime_across_disable(
    tmp_path: Path,
) -> None:
    from argus_skill.daemon.state import disable_continuous_config

    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="finite staged goal",
        open_ended=False,
    )

    state = disable_continuous_config(tmp_path, done_reason="operator drain-stop")

    assert state.open_ended is False
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective=state.objective,
    )
    assert read_continuous_state(tmp_path).open_ended is False


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


def test_continuous_config_cas_preserves_newer_command(tmp_path: Path) -> None:
    from argus_skill.daemon.state import compare_and_swap_continuous_config

    write_continuous_config(tmp_path, enabled=True, objective="older objective")
    expected = read_continuous_state(tmp_path)
    write_continuous_config(tmp_path, enabled=False, objective="newer objective")

    assert (
        compare_and_swap_continuous_config(
            tmp_path,
            expected=expected,
            enabled=True,
            objective="cleaned older objective",
        )
        is False
    )
    assert read_continuous_state(tmp_path) == ContinuousConfigState(
        enabled=False,
        objective="newer objective",
    )


def test_continuous_config_cas_detects_same_value_command(tmp_path: Path) -> None:
    from argus_skill.daemon.state import compare_and_swap_continuous_config

    write_continuous_config(tmp_path, enabled=False, objective="paused objective")
    expected = read_continuous_state(tmp_path)
    write_continuous_config(tmp_path, enabled=False, objective="paused objective")

    assert (
        compare_and_swap_continuous_config(
            tmp_path,
            expected=expected,
            enabled=True,
            objective="clean objective",
        )
        is False
    )
    assert read_continuous_state(tmp_path).generation > expected.generation


def test_continuous_config_callback_rollback_restores_generation(
    tmp_path: Path,
) -> None:
    from argus_skill.daemon.state import compare_and_swap_continuous_config

    write_continuous_config(tmp_path, enabled=True, objective="objective")
    expected = read_continuous_state(tmp_path)

    with pytest.raises(RuntimeError, match="commit failed"):
        compare_and_swap_continuous_config(
            tmp_path,
            expected=expected,
            enabled=True,
            objective="clean objective",
            before_write=lambda: (_ for _ in ()).throw(RuntimeError("commit failed")),
        )

    restored = read_continuous_state(tmp_path)
    assert restored.enabled == expected.enabled
    assert restored.objective == expected.objective
    assert restored.generation == expected.generation


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
                enabled, objective, open_ended = self.config.continuous_config_provider()
                self.config.continuous = enabled
                self.config.open_ended = open_ended
                if objective:
                    self.config.continuous_objective = objective
            seen["continuous"].append((self.config.continuous, self.config.continuous_objective))
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

    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.01))
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


def test_life_worker_retries_planning_after_planner_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="keep going",
    )
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task=task,
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        ),
    )

    seen: dict[str, Any] = {"runs": 0, "continuous": []}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["runs"] += 1
            if self.config.continuous_config_provider is not None:
                enabled, objective, open_ended = self.config.continuous_config_provider()
                self.config.continuous = enabled
                self.config.open_ended = open_ended
                if objective:
                    self.config.continuous_objective = objective
            seen["continuous"].append((self.config.continuous, self.config.continuous_objective))
            if seen["runs"] == 1:
                return {"stopped_by": "planner_error"}
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(
        # resume_continuous=True == a supervisor's crash/reboot self-heal launch:
        # the ONLY path that (correctly) adopts the project's persisted campaign.
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()
    state = read_continuous_state(tmp_path)

    assert rc == 0
    assert seen["runs"] == 2
    assert seen["continuous"][0][0] is True
    assert seen["continuous"][1][0] is True
    assert state.enabled is True
    assert state.objective == "keep going"
    assert state.done_reason == ""


def test_resume_continuous_adopts_persisted_manager_handoff_without_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="manager-clean execution objective",
    )
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "life.manager.intent.completed",
                    "intent_id": "intent-original",
                    "continuous_generation": 1,
                    "execution_task": "manager-clean execution objective",
                    "vertical": "research",
                }
            )
            + "\n"
        )
    (tmp_path / "manager-handoff.json").write_text(
        json.dumps(
            {
                "version": 1,
                "objective_sha256": "stale",
                "vertical": "research",
                "continuous_generation": 0,
                "intent_id": "intent-stale",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("persisted resume must not call Manager")
        ),
    )
    seen: dict[str, object] = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["continuous"] = self.config.continuous
            seen["objective"] = self.config.continuous_objective
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            project_workdir=workdir,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert seen == {
        "continuous": True,
        "objective": "manager-clean execution objective",
    }
    assert read_continuous_state(tmp_path).enabled is True
    identity = json.loads((tmp_path / "manager-handoff.json").read_text())
    assert identity["intent_id"] == "intent-original"


def test_resume_with_explicit_new_objective_runs_manager_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="old execution objective",
    )
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "life.manager.intent.completed",
                    "intent_id": "intent-old",
                    "continuous_generation": 1,
                    "execution_task": "old execution objective",
                    "vertical": "research",
                }
            )
            + "\n"
        )
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    calls: list[str] = []

    def decide_vertical(_self, task, **_kwargs):
        calls.append(task)
        return SimpleNamespace(
            execution_task="new manager-clean objective",
            choice="existing",
            vertical="research",
            adaptation_reason="Preserve the established research direction.",
        )

    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        decide_vertical,
    )
    commit_kwargs: list[dict[str, object]] = []

    def commit_vertical_decision(self, task, decision, **kwargs):
        commit_kwargs.append(dict(kwargs))
        return SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        )

    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        commit_vertical_decision,
    )
    seen: dict[str, object] = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]
            self._vertical_resolved = False

        def run(self) -> dict[str, Any]:
            seen["objective"] = self.config.continuous_objective
            seen["vertical_resolved"] = self._vertical_resolved
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            project_workdir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            continuous=True,
            continuous_objective="new raw objective",
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert calls == ["new raw objective"]
    assert commit_kwargs[0]["force_stage_reset"] is True
    assert seen["objective"] == "new manager-clean objective"
    assert seen["vertical_resolved"] is True
    assert read_continuous_state(tmp_path).objective == "new manager-clean objective"
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text().splitlines()
    ]
    completed = [
        event
        for event in events
        if event.get("type") == "life.manager.intent.completed"
    ]
    assert completed[-1]["reason"] == "Preserve the established research direction."


def test_resume_with_additive_objective_preserves_existing_pipeline_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    original = "Develop a submission-quality long-context paper."
    extended = (
        original + "\n\nOperator standing research authority: continue autonomously "
        "and preserve all prior evidence."
    )
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective=original,
    )
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "life.manager.intent.completed",
                    "intent_id": "intent-old",
                    "continuous_generation": 1,
                    "execution_task": original,
                    "vertical": "research",
                }
            )
            + "\n"
        )
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)

    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda _self, _task, **_kwargs: SimpleNamespace(
            execution_task=extended,
            choice="existing",
            vertical="research",
        ),
    )
    commit_kwargs: list[dict[str, object]] = []

    def commit_vertical_decision(self, task, decision, **kwargs):
        commit_kwargs.append(dict(kwargs))
        return SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        )

    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        commit_vertical_decision,
    )

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            continuous=True,
            continuous_objective=extended,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert commit_kwargs[0]["force_stage_reset"] is False
    assert read_continuous_state(tmp_path).objective == extended


def test_terminal_workspace_without_prior_handoff_reopens_for_new_daemon_intent(
    tmp_path: Path,
) -> None:
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "software")
    state_path = tmp_path / ".argus" / "PIPELINE_STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_stage"] = "delivery"
    state["stages"] = {"delivery": {"status": "done"}}
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert (
        life_worker_mod._daemon_objective_requires_stage_reset(
            project_root=tmp_path,
            prior_vertical="software",
            next_vertical="software",
            prior_handoff=None,
            expected_objective="new objective",
            source_objective="new objective",
            execution_task="new objective",
        )
        is True
    )
    assert (
        life_worker_mod._daemon_objective_requires_stage_reset(
            project_root=tmp_path,
            prior_vertical="software",
            next_vertical="software",
            prior_handoff={"objective_sha256": life_worker_mod._objective_sha256("new objective")},
            expected_objective="new objective",
            source_objective="new objective",
            execution_task="new objective",
        )
        is True
    )


def test_manager_handoff_identity_distinguishes_domain() -> None:
    identity = {
        "version": 2,
        "objective_sha256": life_worker_mod._objective_sha256("write a chemistry paper"),
        "vertical": "research",
        "domain": "chemistry",
        "continuous_generation": 3,
    }

    assert life_worker_mod._manager_handoff_identity_matches(
        identity,
        objective="write a chemistry paper",
        vertical="research",
        domain="chemistry",
        generation=3,
    )
    assert not life_worker_mod._manager_handoff_identity_matches(
        identity,
        objective="write a chemistry paper",
        vertical="research",
        domain="",
        generation=3,
    )


def test_life_worker_keeps_continuous_enabled_on_terminal_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="keep going",
    )
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task=task,
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        ),
    )

    seen: dict[str, Any] = {"runs": 0, "continuous": []}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["runs"] += 1
            if self.config.continuous_config_provider is not None:
                enabled, objective, open_ended = self.config.continuous_config_provider()
                self.config.continuous = enabled
                self.config.open_ended = open_ended
                if objective:
                    self.config.continuous_objective = objective
            seen["continuous"].append((self.config.continuous, self.config.continuous_objective))
            self.config.stop_event.set()
            return {"stopped_by": "planner_terminal_idle", "suggested_sleep": 30.0}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(
        # resume_continuous=True == a supervisor's crash/reboot self-heal launch:
        # the ONLY path that (correctly) adopts the project's persisted campaign.
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()
    state = read_continuous_state(tmp_path)

    assert rc == 0
    assert seen["runs"] == 1
    assert seen["continuous"] == [(True, "keep going")]
    assert state.enabled is True
    assert state.objective == "keep going"
    assert state.done_reason == ""


def test_daemon_manager_handoff_does_not_overwrite_newer_continuous_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(tmp_path, enabled=True, objective="older objective")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)

    def decide_vertical(self, task, **kwargs):
        write_continuous_config(
            tmp_path,
            enabled=False,
            objective="newer objective",
        )
        return SimpleNamespace(
            execution_task="cleaned older objective",
            choice="existing",
            vertical="research",
        )

    commits = []
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        decide_vertical,
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: commits.append(task),
    )
    seen = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["continuous"] = self.config.continuous
            seen["objective"] = self.config.continuous_objective
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert seen == {"continuous": False, "objective": ""}
    assert commits == []
    assert read_continuous_state(tmp_path) == ContinuousConfigState(
        enabled=False,
        objective="newer objective",
    )


def test_daemon_boot_uses_state_snapshot_that_produced_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(tmp_path, enabled=True, objective="older objective")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)

    def reset_manager_session(_root):
        write_continuous_config(
            tmp_path,
            enabled=False,
            objective="newer objective",
        )
        return False

    monkeypatch.setattr(
        "argus_skill.manager.reset_manager_session",
        reset_manager_session,
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task="cleaned older objective",
            choice="existing",
            vertical="research",
        ),
    )
    commits = []
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: commits.append(task),
    )

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    state = read_continuous_state(tmp_path)
    assert state.enabled is False
    assert state.objective == "newer objective"
    assert commits == []


def test_daemon_suppresses_rejected_objective_when_handoff_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.daemon import state as daemon_state

    LifeMemory.open(tmp_path).init()
    raw = "older objective; Manager owns the sidebar"
    write_continuous_config(tmp_path, enabled=True, objective=raw)
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task="clean older objective",
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        ),
    )
    monkeypatch.setattr(
        daemon_state,
        "_write_continuous_config_unlocked",
        lambda *args, **kwargs: False,
    )
    seen = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["initial"] = (
                self.config.continuous,
                self.config.continuous_objective,
            )
            enabled, objective, open_ended = self.config.continuous_config_provider()
            seen.update(
                enabled=enabled,
                objective=objective,
                open_ended=open_ended,
            )
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert seen == {
        "initial": (False, ""),
        "enabled": False,
        "objective": "",
        "open_ended": True,
    }
    assert read_continuous_state(tmp_path).enabled is True


def test_daemon_manager_decision_failure_preserves_persisted_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    LifeMemory.open(tmp_path).init()
    raw = "objective; Manager owns the sidebar"
    write_continuous_config(tmp_path, enabled=True, objective=raw)
    before = read_continuous_state(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("backend unavailable")),
    )
    seen = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen.update(
                enabled=self.config.continuous,
                objective=self.config.continuous_objective,
            )
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    caplog.set_level("INFO")
    assert worker.run_forever() == 0
    after = read_continuous_state(tmp_path)
    assert seen == {"enabled": False, "objective": ""}
    assert after.enabled is True
    assert after.objective == raw
    assert after.generation == before.generation
    assert "daemon: degraded" in caplog.text
    assert "daemon: ready" not in caplog.text
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    degraded = [event for event in events if event["type"] == "life.daemon.degraded"]
    assert len(degraded) == 1
    assert degraded[0]["objective_dispatched"] is False


def test_daemon_boot_leaves_paused_objective_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="paused objective",
    )
    before = read_continuous_state(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("paused objective must not be processed at boot")
        ),
    )
    seen: dict[str, object] = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["initial"] = (
                self.config.continuous,
                self.config.continuous_objective,
            )
            provider = self.config.continuous_config_provider
            assert provider is not None
            enabled, objective, open_ended = provider()
            seen["provider"] = (enabled, objective, open_ended)
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    after = read_continuous_state(tmp_path)
    assert after.enabled is False
    assert after.objective == "paused objective"
    assert after.generation == before.generation
    assert seen == {
        "initial": (False, ""),
        "provider": (False, "", True),
    }


def test_concluded_handoff_resume_does_not_repeat_disabled_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="already concluded mission",
        done_reason="planner declared project done",
    )
    before = read_continuous_state(tmp_path)
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "argus_maintenance")
    life_worker_mod._write_manager_handoff_identity(
        tmp_path,
        objective="already concluded mission",
        vertical="argus_maintenance",
        domain="",
        continuous_generation=before.generation,
        intent_id="intent-concluded-mission",
    )
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("concluded objective must not be processed at boot")
        ),
    )
    seen: dict[str, object] = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["initial"] = (
                self.config.continuous,
                self.config.continuous_objective,
            )
            provider = self.config.continuous_config_provider
            assert provider is not None
            enabled, objective, open_ended = provider()
            seen["provider"] = (enabled, objective, open_ended)
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    after = read_continuous_state(tmp_path)
    assert after.enabled is False
    assert after.objective == "already concluded mission"
    assert after.done_reason == "planner declared project done"
    assert after.generation == before.generation
    assert seen == {
        "initial": (False, ""),
        "provider": (False, "", True),
    }


def test_project_done_does_not_disable_newer_same_value_rearm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(tmp_path, enabled=True, objective="objective")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task=task,
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        ),
    )

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            write_continuous_config(
                tmp_path,
                enabled=True,
                objective="objective",
            )
            self.config.stop_event.set()
            return {"stopped_by": "project_done"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    state = read_continuous_state(tmp_path)
    assert state.enabled is True
    assert state.objective == "objective"


def test_project_done_is_persisted_before_self_maintenance_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="objective",
    )
    before = read_continuous_state(tmp_path)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            project_workdir=tmp_path,
        )
    )
    worker._adopted_continuous_generation = before.generation

    class FakeBudget:
        def can_start(self, **_kwargs):
            return True, ""

    class FakeSupervisor:
        config = SimpleNamespace(budget=FakeBudget())
        _missions_started = 0
        _planning_cycles = 0

        def run(self):
            return {"stopped_by": "project_done"}

    class FakeMaintenance:
        def reconcile_pull_request(self):
            return ""

        def audit_if_due(self, **_kwargs):
            return f"adopt:{candidate}"

        def publish_after_canary(self, **_kwargs):
            return ""

        def mark_handoff_failed(self, _reason):
            pytest.fail("handoff should succeed")

    monkeypatch.setattr(
        life_worker_mod,
        "_spawn_handoff_candidate",
        lambda *_args, **_kwargs: True,
    )
    worker._self_maintenance = FakeMaintenance()
    rf_state = SimpleNamespace(
        runtime_root=tmp_path,
        cfg=worker.config,
        runner=SimpleNamespace(manager=None),
        sup=FakeSupervisor(),
    )

    worker._rf_main_loop(rf_state)

    after = read_continuous_state(tmp_path)
    assert after.enabled is False
    assert after.done_reason == "planner declared project done"


def test_bounded_daemon_exits_after_project_done(
    tmp_path: Path,
) -> None:
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            project_workdir=tmp_path,
            poll_interval=0.0,
            continuous_open_ended=False,
        )
    )
    worker._self_maintenance = None
    worker._curator = None
    calls = 0

    class FakeSupervisor:
        config = SimpleNamespace(budget=SimpleNamespace(can_start=lambda **_kwargs: (True, "")))
        _missions_started = 0
        _planning_cycles = 0

        def run(self):
            nonlocal calls
            calls += 1
            return {"stopped_by": "project_done"}

    rf_state = SimpleNamespace(
        runtime_root=tmp_path,
        cfg=worker.config,
        runner=SimpleNamespace(manager=None),
        sup=FakeSupervisor(),
    )

    worker._rf_main_loop(rf_state)

    assert calls == 1


def test_open_ended_daemon_stays_resident_after_project_done(
    tmp_path: Path,
) -> None:
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            project_workdir=tmp_path,
            poll_interval=0.0,
            continuous_open_ended=True,
        )
    )
    worker._self_maintenance = None
    worker._curator = None
    calls = 0

    class FakeSupervisor:
        config = SimpleNamespace(budget=SimpleNamespace(can_start=lambda **_kwargs: (True, "")))
        _missions_started = 0
        _planning_cycles = 0

        def run(self):
            nonlocal calls
            calls += 1
            if calls == 2:
                worker._stop.set()
            return {"stopped_by": "project_done"}

    rf_state = SimpleNamespace(
        runtime_root=tmp_path,
        cfg=worker.config,
        runner=SimpleNamespace(manager=None),
        sup=FakeSupervisor(),
    )

    worker._rf_main_loop(rf_state)

    assert calls == 2


def test_daemon_stop_does_not_log_drain_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            project_workdir=tmp_path,
            poll_interval=0.0,
        )
    )
    worker._self_maintenance = None
    worker._curator = None

    class FakeSupervisor:
        _missions_started = 0
        _planning_cycles = 0

        def run(self):
            worker._stop.set()
            raise RuntimeError("interrupted work")

    rf_state = SimpleNamespace(
        runtime_root=tmp_path,
        cfg=worker.config,
        runner=SimpleNamespace(manager=None),
        sup=FakeSupervisor(),
    )

    worker._rf_main_loop(rf_state)

    assert "drain pass raised" not in caplog.text


def test_operator_stop_freezes_adopted_generation_before_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    write_continuous_config(tmp_path, enabled=True, objective="objective")
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task=task,
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus_skill.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: SimpleNamespace(
            execution_task=decision.execution_task,
            vertical=decision.vertical,
            kind="research",
            stages=[],
        ),
    )
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            resume_continuous=True,
        )
    )

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            worker._operator_stop_requested = True
            write_continuous_config(
                tmp_path,
                enabled=True,
                objective="objective",
            )
            self.config.continuous_config_provider()
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert read_continuous_state(tmp_path).enabled is True


def test_continuous_mode_error_allows_memory_backend_only_in_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")

    assert (
        life_worker_mod.continuous_mode_error(
            "memory",
            True,
            "keep going",
        )
        == ""
    )


def test_no_pid_file_means_status_dead(tmp_path: Path) -> None:
    pid_path = tmp_path / "daemon.pid"
    assert not pid_path.exists()
    assert read_daemon_status(tmp_path).alive is False
    if pid_path.exists():  # pragma: no cover
        os.unlink(pid_path)


def _wait_for_thread_stop(thread: threading.Thread, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return
        time.sleep(0.05)


def _wait_for_backlog_item_status(
    mem: LifeMemory,
    item_id: str,
    status: str,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = next((candidate for candidate in mem.backlog.all() if candidate.id == item_id), None)
        if item is not None and item.status == status:
            return
        time.sleep(0.05)
    observed = {item.id: item.status for item in mem.backlog.all()}
    raise AssertionError(f"item {item_id} did not reach {status}: {observed}")


def test_strip_git_config_injection_removes_whole_family() -> None:
    """The codex sandbox forwards an incomplete ``GIT_CONFIG_*`` tuple that
    breaks every ``git`` command in the agent shell. The whole family must be
    stripped from the child env, leaving unrelated git vars untouched.
    """

    env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.bareRepository",
        "GIT_CONFIG_VALUE_0": "explicit",
        "GIT_CONFIG_KEY_1": "core.foo",
        "GIT_CONFIG_VALUE_1": "bar",
        "GIT_DIR": "/keep/me",
        "PATH": "/usr/bin",
    }

    removed = _strip_git_config_injection(env)

    assert set(removed) == {
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
    }
    assert env == {"GIT_DIR": "/keep/me", "PATH": "/usr/bin"}


def test_strip_git_config_injection_noop_when_absent() -> None:
    env = {"PATH": "/usr/bin", "HOME": "/home/x"}
    removed = _strip_git_config_injection(env)
    assert removed == []
    assert env == {"PATH": "/usr/bin", "HOME": "/home/x"}


# ---------------------------------------------------------------------------
# Regression: _runner_namespace must propagate open_ended + continuous_objective
# ---------------------------------------------------------------------------


def test_runner_namespace_propagates_open_ended_and_continuous_objective(
    tmp_path: Path,
) -> None:
    """_runner_namespace must copy LifeWorkerConfig.continuous_open_ended → ns.open_ended
    and LifeWorkerConfig.continuous_objective → ns.continuous_objective unchanged.

    Regression: before the fix these attributes were absent from the namespace, so
    _SkillLoopRunner.execute used getattr(args, "open_ended", False) == False even
    for daemon-created open-ended campaigns, causing the Manager's rollback decisions
    at the final stage to be silently overwritten by bounded final completion.
    """
    ns = _runner_namespace(
        LifeWorkerConfig(
            life_dir=tmp_path / "life",
            backend="memory",
            continuous_open_ended=True,
            continuous_objective="keep proving X",
        )
    )
    assert ns.open_ended is True
    assert ns.continuous_objective == "keep proving X"


def test_runner_namespace_open_ended_false_for_bounded_daemon(tmp_path: Path) -> None:
    """A bounded daemon (continuous_open_ended=False) must produce open_ended=False."""
    ns = _runner_namespace(
        LifeWorkerConfig(
            life_dir=tmp_path / "life",
            backend="memory",
            continuous_open_ended=False,
            continuous_objective="",
        )
    )
    assert ns.open_ended is False
    assert ns.continuous_objective == ""
