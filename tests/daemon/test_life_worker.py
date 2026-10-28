"""Smoke tests for the 7×24 life worker."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import argus.daemon.life_worker as life_worker_mod
from argus.core.session import SessionMeta, write_session_meta
from argus.daemon.life_worker import (
    DaemonStatus,
    LifeWorker,
    LifeWorkerConfig,
    _config_payload,
    _DaemonSink,
    _runner_namespace,
    _strip_git_config_injection,
    _worker_runtime_context,
    _workspace_start_error,
    read_continuous_state,
    read_daemon_status,
)
from argus.daemon.state import (
    DAEMON_UPGRADE_REQUEST_FILE,
    _daemon_status_payload,
    daemon_drain_requested,
    request_daemon_stop,
)
from argus.life.memory import BacklogItem, LifeMemory

_ENV_VARS_TO_CLEAR = (
    "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG",
    "ARGUS_SKILL_DAEMON_HANDOFF_READY",
    "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN",
    "ARGUS_SKILL_ENGINEER_MODEL",
    "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
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
def _clear_ambient_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    # Deleting ARGUS_SKILL_HOME alone makes core.paths.global_root() fall back
    # to the REAL ~/.argus-skill, so persisted host knobs (e.g. a pinned
    # ARGUS_SKILL_SOURCE_ROOT in config.json) leak into every test — the
    # daemon's source-root preflight then refuses to boot (rc=2). Point the
    # runtime root at an empty per-test directory instead; tests that
    # exercise default-root resolution or the worker's own export override
    # this (monkeypatching global_root, or delenv'ing again themselves).
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "ambient-home"))


def test_daemon_refuses_a_mismatched_configured_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the real preflight: a hijacked launcher loads some
    other worktree, so the configured deployment root no longer matches the
    loaded package and the daemon must not come up."""
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(tmp_path / "other-worktree"))
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))

    assert worker._rf_vault_preflight(SimpleNamespace(cfg=worker.config)) == 2


def test_daemon_bootstrap_pins_pip_user_off_for_inherited_child_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 7x24 maintenance engineer runs dangerous_yolo by default: the codex
    child inherits os.environ untouched, so PIP_USER=0 (the guard against
    pip's silent user-install launcher hijack, 2026-09-05) only reaches it if
    the daemon's own boot pins it. Exercise the real boot entry — dropping the
    configure_framework_python_env call from _rf_bootstrap_environment must
    fail HERE, not only in unit tests of the helper."""
    monkeypatch.setenv("PIP_USER", "1")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    # The env wiring is under test; keep the pytest process's own handlers.
    monkeypatch.setattr(LifeWorker, "_install_signal_handlers", lambda self: None)
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))

    worker._rf_bootstrap_environment()

    assert os.environ["PIP_USER"] == "0"


def test_daemon_status_payload_preserves_life_backend(tmp_path: Path) -> None:
    payload = _daemon_status_payload(
        LifeWorkerConfig(life_dir=tmp_path, backend="memory"),
        started_at_iso="2026-07-20T00:00:00+00:00",
    )

    assert payload["life_backend"] == "memory"


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


def test_read_daemon_status_rejects_sidecar_from_different_pid(
    tmp_path: Path,
) -> None:
    from argus.core.daemon_lock import acquire_global_daemon_lock

    with acquire_global_daemon_lock(pid_path=tmp_path / "daemon.pid") as lock:
        (tmp_path / "daemon.status.json").write_text(
            json.dumps(
                {
                    "pid": lock.pid + 1,
                    "backend": "stale-backend",
                }
            ),
            encoding="utf-8",
        )
        status = read_daemon_status(tmp_path)

    assert status.alive is True
    assert status.pid == lock.pid
    assert status.backend is None
    assert "does not match lock pid" in status.status_read_error


def test_nonblocking_stop_request_revalidates_daemon_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.daemon.state as daemon_state

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
    import argus.daemon.state as daemon_state

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
    from argus.daemon import state as daemon_state

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


def test_multi_supervisor_pass_delegates_pipeline_lock_reentry(
    tmp_path: Path,
) -> None:
    """The multi-supervisor drain pass must hand workers its lock entitlement.

    ``_rf_main_loop`` holds ``manager_pipeline_lock`` while awaiting its
    supervisor futures, and each mission pass re-enters that same lock (the
    2026-09-05 paper-daemon incident shape). Re-entry rides the DELEGATION
    CHAIN — a ContextVar — so the submit site must run each worker in a copy
    of the holder's context (``executor.submit(copy_context().run, ...)``).
    With a plain submit the workers carry a fresh context, block on the flock
    the main loop is holding, and the pass never completes: this test then
    sees ``reentry_timeout`` outcomes / a still-spinning loop and fails.
    """
    import contextvars

    from argus.manager._session_ops import manager_pipeline_lock

    outcomes: list[str] = []
    outcomes_mutex = threading.Lock()

    class FakeSupervisor:
        def __init__(self, worker_id: str) -> None:
            self.config = SimpleNamespace(worker_id=worker_id)
            self._missions_started = 0
            self._planning_cycles = 0

        def run(self) -> dict:
            # Probe the re-entry from a bounded side thread that inherits
            # THIS worker's context: if the executor submit propagated the
            # delegation chain, the probe enters the gate and finishes; if
            # not, it polls the flock forever and we time out instead of
            # deadlocking the whole pytest process (the flock is held by the
            # main loop until this run() returns).
            reentered = threading.Event()

            def _reenter() -> None:
                with manager_pipeline_lock(tmp_path):
                    reentered.set()

            probe = threading.Thread(
                target=contextvars.copy_context().run,
                args=(_reenter,),
                daemon=True,
            )
            probe.start()
            stopped_by = (
                "backlog_empty" if reentered.wait(timeout=8.0) else "reentry_timeout"
            )
            with outcomes_mutex:
                outcomes.append(stopped_by)
            return {"stopped_by": stopped_by, "suggested_sleep": 0}

    cfg = LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.05,
        continuous_open_ended=False,
    )
    worker = LifeWorker(cfg)
    sup_primary = FakeSupervisor("primary")
    sup_secondary = FakeSupervisor("secondary")
    rf_state = SimpleNamespace(
        cfg=cfg,
        runtime_root=tmp_path,
        runner=SimpleNamespace(
            manager=SimpleNamespace(
                pipeline_lock=lambda: manager_pipeline_lock(tmp_path),
            ),
        ),
        sup=sup_primary,
        supervisors=[sup_primary, sup_secondary],
    )

    loop = threading.Thread(
        target=worker._rf_main_loop, args=(rf_state,), daemon=True
    )
    loop.start()
    loop.join(timeout=25.0)
    loop_completed = not loop.is_alive()
    worker._stop.set()  # unwind a still-spinning red-path loop
    loop.join(timeout=25.0)
    assert loop_completed, (
        "multi-supervisor drain pass did not finish: workers were submitted "
        "without the holder's context and pipeline-lock re-entry blocked"
    )
    assert outcomes[:2] == ["backlog_empty", "backlog_empty"], (
        f"supervisor passes failed to re-enter manager_pipeline_lock: {outcomes!r}"
    )


def test_daemon_sink_counts_life_mission_completed() -> None:
    cfg = LifeWorkerConfig(life_dir=Path("/tmp"), backend="memory")
    worker = LifeWorker(cfg)
    sink = _DaemonSink(worker)

    sink.handle_event({"type": "life.mission.completed"})

    assert worker._missions_completed == 1


def test_daemon_requeues_running_item_when_executor_thread_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = BacklogItem.new(title="stalled", objective="finish the task")
    memory.backlog.add(item)
    memory.backlog.mark_running(item.id)
    events: list[dict[str, Any]] = []
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    dead_executor = threading.Thread(target=lambda: None)
    dead_executor.start()
    dead_executor.join()
    worker._supervisor_execution_threads = {"primary": dead_executor}
    state = SimpleNamespace(
        mem=memory,
        runtime_root=tmp_path,
        sink=SimpleNamespace(handle_event=events.append),
    )
    monkeypatch.setattr(
        "argus.daemon._life_worker_run._RUNNING_STALL_POLL_SECONDS",
        0.01,
    )

    worker._start_running_stall_watcher(state)
    try:
        _wait_for_backlog_item_status(memory, item.id, "pending", timeout=1.0)
    finally:
        worker._stop_running_stall_watcher()

    stored = next(row for row in memory.backlog.active() if row.id == item.id)
    assert stored.status == "pending"
    assert stored.attempt == 2
    assert stored.last_error == "executor exited without completing the task"
    assert events == []


def test_daemon_does_not_fail_quiet_item_while_supervisor_is_running(
    tmp_path: Path,
) -> None:
    memory = LifeMemory.open(tmp_path)
    memory.init()
    item = BacklogItem.new(title="quiet", objective="wait for external workers")
    memory.backlog.add(item)
    memory.backlog.mark_running(item.id)
    memory.backlog.update(item.id, started_ts=time.time() - 31.0)
    events: list[dict[str, Any]] = []
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    state = SimpleNamespace(
        mem=memory,
        runtime_root=tmp_path,
        sink=SimpleNamespace(handle_event=events.append),
    )
    worker._supervisor_execution_active.set()

    assert worker._recover_stalled_running_items(state) == []
    stored = next(row for row in memory.backlog.active() if row.id == item.id)
    assert stored.status == "running"
    assert events == []


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
    monkeypatch.setattr("argus.life.telegram_bot.TelegramPoller.start", _boom)
    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)

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
    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
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


def test_life_worker_separates_boundary_stop_from_mission_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "1")
    monkeypatch.setattr(
        "argus.core.backend_readiness.check_backend_readiness",
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
        "argus.apps._runtime.build_life_runner",
        fake_build_life_runner,
    )
    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()

    assert captured["stop_event"] is worker._mission_stop
    assert captured["supervisor_stop_event"] is worker._stop
    assert rc == 0


def test_format_short_duration() -> None:
    from argus.apps.cli._core import _format_short_duration

    assert _format_short_duration(0) == "0s"
    assert _format_short_duration(45) == "45s"
    assert _format_short_duration(125) == "2m 5s"
    assert _format_short_duration(3725) == "1h 2m"
    assert _format_short_duration(90061) == "1d 1h"


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

    from argus.core.campaign_workdir import adopt_campaign_workdir

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
        )

    context = _worker_runtime_context(cfg)

    assert "Runtime info" in context
    assert "Engineer model: gpt-5.4-mini" in context
    assert "profile_name: custom-public-profile" in context
    assert "profile_sha256:" not in context


def test_failed_handoff_rolls_back_only_to_this_runs_prior_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus.daemon.handoff as handoff_mod

    cfg = LifeWorkerConfig(life_dir=tmp_path / "life", backend="memory")
    cfg.life_dir.mkdir(parents=True)
    rollback = tmp_path / "prior-source"
    rollback.mkdir()
    deployed = tmp_path / "deployed-source"
    deployed.mkdir()
    gate_spawned: dict[str, object] = {}

    def fake_gate_spawn(config, **kwargs):
        gate_spawned["config"] = config
        gate_spawned.update(kwargs)
        return True

    handoff_mod.request_deployment_handoff(
        cfg.life_dir / "maintenance" / "receipts",
        deployed,
    )
    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", fake_gate_spawn)
    monkeypatch.setattr(
        "argus.core.runtime_identity.source_root",
        lambda: rollback,
    )
    worker = LifeWorker(cfg)

    assert worker._deployment_handoff_gate() == "daemon_handoff"
    assert gate_spawned["candidate_source_root"] == deployed
    assert gate_spawned["rollback_source_root"] == rollback
    assert not (cfg.life_dir / "maintenance" / "receipts" / "daemon-roll.json").exists()

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
    monkeypatch.setenv(handoff_mod._HANDOFF_ROLLBACK_SOURCE_ENV, str(rollback))
    monkeypatch.setattr(
        life_worker_mod,
        "_acquire_daemon_lock_with_timeout",
        lambda *_args: FakeLock(),
    )
    monkeypatch.setattr(LifeWorker, "run_forever", lambda _self: 2)
    monkeypatch.setattr(handoff_mod, "_spawn_handoff_candidate", fake_spawn)

    assert life_worker_mod.run_handoff_child() == 2
    assert spawned["candidate_source_root"] == rollback
    assert "rollback_source_root" not in spawned


def test_daemon_pid_path(tmp_path: Path) -> None:
    from argus.daemon.life_worker import _daemon_pid_path

    assert _daemon_pid_path(tmp_path).name == "daemon.pid"


# ---------------------------------------------------------------------------
# Continuous config (disk-based hot-reload)
# ---------------------------------------------------------------------------

from argus.daemon.life_worker import read_continuous_config, write_continuous_config


def test_read_continuous_config_malformed(tmp_path: Path) -> None:
    (tmp_path / "continuous.json").write_text("not json at all")
    enabled, obj = read_continuous_config(tmp_path)
    assert enabled is False
    assert obj == ""


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

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)

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
    from argus.skills.vertical_select import persist_vertical

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
                "version": 4,
                "objective": "stale",
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
        "argus.manager.Manager.decide_vertical",
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

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
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
    objective_file = tmp_path / "OBJECTIVE.md"
    objective_file.write_text("new raw objective", encoding="utf-8")
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="old execution objective",
    )
    from argus.skills.vertical_select import persist_vertical

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
        "argus.manager.Manager.decide_vertical",
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
        "argus.manager.Manager.commit_vertical_decision",
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

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            project_workdir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            continuous=True,
            continuous_objective="new raw objective",
            continuous_objective_file=objective_file,
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
    identity = json.loads((tmp_path / "manager-handoff.json").read_text())
    assert identity["version"] == 4
    assert identity["source_objective_path"] == str(objective_file.resolve())
    assert identity["source_objective"] == "new raw objective"
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


def test_terminal_workspace_without_prior_handoff_reopens_for_new_daemon_intent(
    tmp_path: Path,
) -> None:
    from argus.skills.vertical_select import persist_vertical

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
            prior_handoff={"objective": "new objective"},
            expected_objective="new objective",
            source_objective="new objective",
            execution_task="new objective",
        )
        is True
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

    def persisted_vertical(_root):
        write_continuous_config(
            tmp_path,
            enabled=False,
            objective="newer objective",
        )
        return None

    monkeypatch.setattr(
        "argus.skills.vertical_select._persisted_vertical",
        persisted_vertical,
    )
    monkeypatch.setattr(
        "argus.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task="cleaned older objective",
            choice="existing",
            vertical="research",
        ),
    )
    commits = []
    monkeypatch.setattr(
        "argus.manager.Manager.commit_vertical_decision",
        lambda self, task, decision, **kwargs: commits.append(task),
    )

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
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
    from argus.daemon import state as daemon_state

    LifeMemory.open(tmp_path).init()
    raw = "older objective; Manager owns the sidebar"
    write_continuous_config(tmp_path, enabled=True, objective=raw)
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task="clean older objective",
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus.manager.Manager.commit_vertical_decision",
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

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
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
        "argus.manager.Manager.decide_vertical",
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

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
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


def test_bounded_daemon_exits_when_manager_objective_is_not_dispatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    LifeMemory.open(tmp_path).init()
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(
        "argus.manager.Manager.decide_vertical",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("invalid Manager route")
        ),
    )

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self) -> dict[str, Any]:
            raise AssertionError("bounded handoff failure must not drain")

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            continuous=True,
            continuous_objective="write the bounded paper package",
            continuous_open_ended=False,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 2
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failed = [event for event in events if event["type"] == "life.manager.intent.failed"]
    assert len(failed) == 1
    assert "event_validation" not in failed[0]


def test_fresh_project_daemon_objective_is_dispatched_on_the_managers_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``argus --daemon --continuous --objective ... --bounded`` on a new project.

    Nothing routed it yet, so the daemon asks the Manager for its ticket, as
    the front door does, instead of failing with "Waiting for the Manager's
    ticket" before any model call.
    """
    from argus.apps._runtime_backends import _MemoryRunner
    from argus.skills.vertical_select import resolve_vertical_if_decided
    from tests.hand_off import ticket

    global_root = tmp_path / "home"
    life_dir = global_root / "projects" / "demo"
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(global_root))
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)
    turns: list[str] = []

    def manager_turn(self, *, message, actions, **_kwargs):
        turns.append(message)
        actions.dispatch(*ticket("Write hello.py that prints hello."))
        return SimpleNamespace(success=True, stop_reason="", delivery=None)

    monkeypatch.setattr(_MemoryRunner, "manager_turn", manager_turn, raising=False)
    seen: dict[str, Any] = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["objective"] = self.config.continuous_objective
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=global_root,
            project_fingerprint="demo",
            project_workdir=workdir,
            backend="memory",
            poll_interval=0.01,
            continuous=True,
            continuous_objective="write hello.py",
            continuous_open_ended=False,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert len(turns) == 1 and turns[0].startswith("write hello.py")
    assert seen["objective"].startswith("Write hello.py that prints hello.")
    assert resolve_vertical_if_decided(life_dir) == "software"
    events = [
        json.loads(line)
        for line in (life_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [event["type"] for event in events]
    assert "life.manager.intent.completed" in types
    assert "life.manager.intent.failed" not in types


def test_fresh_project_daemon_objective_without_a_ticket_is_not_dispatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.apps._runtime_backends import _MemoryRunner

    global_root = tmp_path / "home"
    life_dir = global_root / "projects" / "demo"
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(global_root))
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_TEST_ALLOW_MEMORY_CONTINUOUS", "1")
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_TELEGRAM_CHAT_ID", raising=False)

    def manager_turn(self, *, sink, **_kwargs):
        # The Manager only replies; no one reads a reply at daemon boot.
        sink.handle_event({"type": "round.main.completed", "exit_code": 0, "last_message": "Sure."})
        return SimpleNamespace(success=True, stop_reason="", delivery=None)

    monkeypatch.setattr(_MemoryRunner, "manager_turn", manager_turn, raising=False)

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self) -> dict[str, Any]:
            raise AssertionError("an objective without a ticket must not drain")

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=life_dir,
            global_root=global_root,
            project_fingerprint="demo",
            project_workdir=tmp_path,
            backend="memory",
            poll_interval=0.01,
            continuous=True,
            continuous_objective="write hello.py",
            continuous_open_ended=False,
        )
    )
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 2
    failed = [
        json.loads(line)
        for line in (life_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if '"life.manager.intent.failed"' in line
    ]
    assert len(failed) == 1 and "no ticket for the team" in json.dumps(failed[0])


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
    worker._curator = None
    calls = 0

    class FakeSupervisor:
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


def test_bounded_daemon_runs_an_old_background_job_pause_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus.life.event_log import JsonlEventSink
    from argus.life.supervisor import LifeBudget, LifeSupervisor, LifeSupervisorConfig
    from argus.skills.vertical_select import persist_vertical

    life = tmp_path / "life"
    project = tmp_path / "project"
    project.mkdir()
    memory = LifeMemory.open(life)
    persist_vertical(project, "software", workflow_mode="direct")
    write_continuous_config(
        life, enabled=False, objective="an earlier completed campaign",
        open_ended=False, done_reason="planner declared project done",
    )
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=life, backend="memory", project_workdir=project,
        poll_interval=0.0, continuous_open_ended=False,
    ))
    worker._curator = None
    supervisor = LifeSupervisor(
        memory=memory,
        runner=SimpleNamespace(),
        sink=JsonlEventSink(None, life_dir=life, verbosity="full"),
        config=LifeSupervisorConfig(
            budget=LifeBudget(), poll_interval_seconds=0.0,
            continuous=False, open_ended=False, final_certification_gate=False,
            project_worktree=project, artifact_root=life, stop_event=worker._stop,
        ),
    )
    supervisor._vertical_resolved = True
    # An older release parked the task on a job it watched for the Engineer;
    # the Engineer now waits on its own jobs, so the task simply runs again.
    item = memory.backlog.add(BacklogItem.new(
        title="Assess the existing build", objective="Check the completed build",
    ))
    memory.backlog.update(
        item.id, park_reason="external_work",
        outcome={"external_wait": {"work_id": "data-build", "workdir": str(project)}},
    )
    executed: list[str] = []

    def run_one(current):
        executed.append(current.id)
        memory.backlog.mark_done(current.id)
        return {"item_id": current.id, "status": "done", "success": True}

    monkeypatch.setattr(supervisor, "_run_one", run_one)
    sleeps: list[float] = []

    def sleep(seconds, _poll, _root, *, wake_on_ready_work=False):
        sleeps.append(seconds)
        worker._stop.set()

    monkeypatch.setattr(worker, "_wakeable_sleep", sleep)
    worker._rf_main_loop(SimpleNamespace(
        runtime_root=life, cfg=worker.config,
        runner=SimpleNamespace(manager=None), sup=supervisor,
    ))

    assert sleeps == []
    assert executed == [item.id]
    stored = next(row for row in memory.backlog.history() if row.id == item.id)
    assert stored.status == "done"
    assert stored.attempt == 2
    assert read_continuous_state(life).enabled is False


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
        "argus.manager.Manager.decide_vertical",
        lambda self, task, **kwargs: SimpleNamespace(
            execution_task=task,
            choice="existing",
            vertical="research",
        ),
    )
    monkeypatch.setattr(
        "argus.manager.Manager.commit_vertical_decision",
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

    monkeypatch.setattr("argus.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert read_continuous_state(tmp_path).enabled is True


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


# ---------------------------------------------------------------------------
# Regression: _runner_namespace must propagate open_ended + continuous_objective
# ---------------------------------------------------------------------------


def test_runner_namespace_propagates_open_ended_and_continuous_objective(
    tmp_path: Path,
) -> None:
    """An enabled campaign forwards its lifetime and objective unchanged.

    Regression: before the fix these attributes were absent from the namespace, so
    _SkillLoopRunner.execute used getattr(args, "open_ended", False) == False even
    for daemon-created open-ended campaigns, causing the Manager's rollback decisions
    at the final stage to be silently overwritten by bounded final completion.
    """
    ns = _runner_namespace(
        LifeWorkerConfig(
            life_dir=tmp_path / "life",
            backend="memory",
            continuous=True,
            continuous_open_ended=True,
            continuous_objective="keep proving X",
        )
    )
    assert ns.open_ended is True
    assert ns.continuous_objective == "keep proving X"


def _run_primary_pass(worker: LifeWorker, supervisors: list[Any]) -> dict:
    """One primary pass beside the helpers, then the worker's stop."""
    run_primary, helpers = worker._start_helpers(supervisors)
    try:
        return run_primary()
    finally:
        worker._stop.set()
        for helper in helpers:
            helper.join(5.0)
        assert not any(helper.is_alive() for helper in helpers)


def test_idle_helper_polls_while_a_primary_pass_runs_and_sleeps_between(tmp_path: Path) -> None:
    """A helper that finds nothing to claim must look again while the primary
    worker is still busy, so work the primary plans mid-pass is picked up in
    parallel instead of waiting for the primary's whole pass to end. Between
    primary passes it does not spin; the next pass wakes it."""
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.01))
    helper_calls: list[int] = []

    def primary_run() -> dict:
        wanted = len(helper_calls) + 3
        deadline = time.monotonic() + 5.0
        while len(helper_calls) < wanted:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        return {"stopped_by": "backlog_empty"}

    def helper_run() -> dict:
        helper_calls.append(1)
        return {"stopped_by": "backlog_empty", "suggested_sleep": 0.01}

    primary = SimpleNamespace(config=SimpleNamespace(worker_id="primary"), run=primary_run)
    helper = SimpleNamespace(config=SimpleNamespace(worker_id="parallel-1"), run=helper_run)
    run_primary, helpers = worker._start_helpers([primary, helper])
    try:
        assert run_primary() == {"stopped_by": "backlog_empty"}
        after_pass = len(helper_calls)
        time.sleep(0.3)
        # At most the pass that was in flight as the primary pass ended.
        assert len(helper_calls) - after_pass <= 1
        assert run_primary() == {"stopped_by": "backlog_empty"}
    finally:
        worker._stop.set()
        for thread in helpers:
            thread.join(5.0)
    assert not any(thread.is_alive() for thread in helpers)
    assert worker._supervisor_execution_threads == {}


def test_idle_helper_honours_backoff_but_wakes_when_backlog_changes(tmp_path: Path) -> None:
    """A helper told to back off for minutes must not re-run (and re-journal)
    every poll while a long primary pass runs, yet an item the primary makes
    ready mid-pass is still claimed promptly rather than after the backoff."""
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.01))
    backlog_file = tmp_path / "backlog.jsonl"
    backlog_file.write_text("")
    backlog = SimpleNamespace(storage_paths=(backlog_file,))
    primary_release = threading.Event()
    ready_item_claimed = threading.Event()
    helper_calls: list[float] = []

    def primary_run() -> dict:
        time.sleep(0.5)
        # Mid-pass, the primary plans a new ready item.
        backlog_file.write_text('{"id": "ready"}\n')
        assert ready_item_claimed.wait(5.0)
        primary_release.wait(0.2)
        return {"stopped_by": "backlog_empty"}

    def helper_run() -> dict:
        helper_calls.append(time.monotonic())
        if backlog_file.read_text():
            ready_item_claimed.set()
            primary_release.set()
        return {"stopped_by": "claim_lost", "suggested_sleep": 300.0}

    primary = SimpleNamespace(config=SimpleNamespace(worker_id="primary"), run=primary_run)
    helper = SimpleNamespace(
        config=SimpleNamespace(worker_id="parallel-1"), run=helper_run, memory=SimpleNamespace(backlog=backlog),
    )

    started = time.monotonic()
    summary = _run_primary_pass(worker, [primary, helper])

    assert summary == {"stopped_by": "backlog_empty"}
    assert ready_item_claimed.is_set()
    # One idle pass, then the pass woken by the backlog change; no spinning.
    assert len(helper_calls) == 2
    assert time.monotonic() - started < 5.0
    assert worker._supervisor_execution_threads == {}


def test_helper_pass_that_raises_is_retried(tmp_path: Path) -> None:
    """A helper's loop outlives a pass that raises, as the main loop outlives
    a failed primary pass: nothing else would start the helper again."""
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.01))
    retried = threading.Event()
    helper_calls: list[int] = []

    def helper_run() -> dict:
        helper_calls.append(1)
        if len(helper_calls) == 1:
            raise RuntimeError("peer inbox unreadable")
        retried.set()
        return {"stopped_by": "backlog_empty"}

    def primary_run() -> dict:
        assert retried.wait(5.0)
        return {"stopped_by": "backlog_empty"}

    primary = SimpleNamespace(config=SimpleNamespace(worker_id="primary"), run=primary_run)
    helper = SimpleNamespace(config=SimpleNamespace(worker_id="parallel-1"), run=helper_run)

    assert _run_primary_pass(worker, [primary, helper]) == {"stopped_by": "backlog_empty"}
    assert len(helper_calls) >= 2


def test_dispatcher_resumes_paused_missions_once_before_any_worker_claims(tmp_path: Path) -> None:
    """With several workers, paused missions are resumed by the dispatcher
    ahead of every worker's pass, not by whichever worker happens to start a
    pass, so each resumed mission is claimable by any free worker."""
    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=5.0))
    order: list[str] = []
    helper_done = threading.Event()

    def primary_run() -> dict:
        order.append("primary")
        assert helper_done.wait(5.0)
        return {"stopped_by": "backlog_empty"}

    def helper_run() -> dict:
        order.append("helper")
        helper_done.set()
        return {"stopped_by": "backlog_empty", "suggested_sleep": 0.01}

    primary = SimpleNamespace(
        config=SimpleNamespace(worker_id="primary"),
        run=primary_run,
        _resume_automatic_pauses=lambda: order.append("resume"),
    )
    helper = SimpleNamespace(config=SimpleNamespace(worker_id="parallel-1"), run=helper_run)

    _run_primary_pass(worker, [primary, helper])

    assert order[0] == "resume"
    assert order.count("resume") == 1
    assert primary._dispatcher_resumes is True
    assert helper._dispatcher_resumes is True


def _bounded_worker_with_scripted_supervisor(tmp_path: Path, outcomes: list[str]):
    worker = LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            project_workdir=tmp_path,
            poll_interval=0.0,
            continuous_open_ended=False,
        )
    )
    worker._curator = None
    calls: list[str] = []

    class FakeSupervisor:
        memory = SimpleNamespace(root=tmp_path)
        _missions_started = 0
        _planning_cycles = 0

        def run(self):
            outcome = outcomes[min(len(calls), len(outcomes) - 1)]
            calls.append(outcome)
            return {"stopped_by": outcome, "suggested_sleep": 0.0}

    rf_state = SimpleNamespace(
        runtime_root=tmp_path,
        cfg=worker.config,
        runner=SimpleNamespace(manager=None),
        sup=FakeSupervisor(),
    )
    return worker, rf_state, calls


def test_bounded_daemon_exits_when_runtime_failure_circuit_is_open(
    tmp_path: Path,
) -> None:
    # The circuit only clears under a different runtime, so a finite worker
    # parked behind it must release its slot instead of waiting forever.
    from argus.life.runtime_failure_circuit import record_runtime_failure_circuit

    try:
        raise FileNotFoundError(2, "No such file or directory", "tool")
    except FileNotFoundError as exc:
        record_runtime_failure_circuit(tmp_path, exc, item_id="item-1")
    worker, rf_state, calls = _bounded_worker_with_scripted_supervisor(
        tmp_path, ["awaiting_external", "backlog_empty"]
    )

    worker._rf_main_loop(rf_state)

    assert calls == ["awaiting_external"]


def test_bounded_daemon_keeps_going_after_an_idle_planner_without_a_circuit(
    tmp_path: Path,
) -> None:
    worker, rf_state, calls = _bounded_worker_with_scripted_supervisor(
        tmp_path, ["awaiting_external", "backlog_empty"]
    )

    worker._rf_main_loop(rf_state)

    assert calls == ["awaiting_external", "backlog_empty"]
