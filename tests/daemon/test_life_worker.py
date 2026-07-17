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

import argus_skill.daemon.life_worker as life_worker_mod
from argus_skill.core.bootstrap import inspect_project_bootstrap
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
    daemon_drain_requested,
    request_daemon_drain,
)
from argus_skill.life.memory import BacklogItem, LifeMemory

_ENV_VARS_TO_CLEAR = (
    "ARGUS_SKILL_DAILY_CAP_USD",
    "ARGUS_SKILL_DAEMON_AUTO_RESTART",
    "ARGUS_SKILL_DAEMON_HANDOFF_CONFIG",
    "ARGUS_SKILL_DAEMON_HANDOFF_GEN",
    "ARGUS_SKILL_DAEMON_HANDOFF_MAX_GEN",
    "ARGUS_SKILL_DAEMON_HANDOFF_MIN_S",
    "ARGUS_SKILL_DAEMON_HANDOFF_READY",
    "ARGUS_SKILL_DAEMON_HANDOFF_TOKEN",
    "ARGUS_SKILL_DAEMON_SOURCE_SIGNATURE",
    "ARGUS_SKILL_DAEMON_TEST_SOURCE_SIGNATURE_FILE",
    "ARGUS_SKILL_ENGINEER_MODEL",
    "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD",
    "ARGUS_SKILL_HOME",
    "ARGUS_SKILL_LIFE_BACKEND",
    "ARGUS_SKILL_MAX_ACTIVE_DAEMONS",
    "ARGUS_SKILL_MAX_ROUNDS",
    "ARGUS_SKILL_MODEL",
    "ARGUS_SKILL_PER_MISSION_CAP_USD",
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

    assert life_worker_mod._max_active_daemons(
        LifeWorkerConfig(life_dir=tmp_path)
    ) == 64


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


def test_inspect_project_bootstrap_leaves_generic_empty_repo_to_agent(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is False
    assert preflight.missing_artifacts == (".git", "build manifest", "README*", "source files")
    assert preflight.bootstrap_objective == ""
    assert preflight.event_text == ""


def test_inspect_project_bootstrap_detects_research_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is True
    assert "research bootstrap mission" in preflight.bootstrap_objective.lower()
    assert "research/PIPELINE_STATE.json" in preflight.bootstrap_objective
    assert "re-check it at execution time" in preflight.bootstrap_objective
    assert "target_venue" in preflight.bootstrap_objective
    assert "research/RESEARCH_BRIEF.md" in preflight.bootstrap_objective
    assert "research/GO_NO_GO.md" in preflight.bootstrap_objective
    assert "research bootstrap requested" in preflight.event_text
    assert "pyproject.toml" not in preflight.bootstrap_objective


def test_inspect_project_bootstrap_ignores_objective_keywords(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Philosophy: the harness must NOT sniff the objective text for keywords
    # like "emnlp" / "auto-research" to choose a research scaffold. Without a
    # structured research profile, workspace shape belongs to the agent.
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()

    preflight = inspect_project_bootstrap(
        repo_dir,
        objective_hint="EMNLP auto-research bootstrap mission",
    )

    assert preflight.should_bootstrap is False
    assert preflight.bootstrap_objective == ""
    assert preflight.event_text == ""


def test_structured_research_candidate_does_not_sniff_objective(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()

    preflight = inspect_project_bootstrap(
        repo_dir,
        objective_hint="写一篇赤壁赋",
        research_requested=True,
    )

    assert preflight.should_bootstrap is True
    assert "research bootstrap mission" in preflight.bootstrap_objective.lower()


def test_inspect_project_bootstrap_heals_partial_research_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    (repo_dir / "research" / "PIPELINE_STATE.json").parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "research" / "PIPELINE_STATE.json").write_text(
        "{\n  \"current_stage\": \"plan\"\n}\n",
        encoding="utf-8",
    )

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is True
    assert "research bootstrap mission" in preflight.bootstrap_objective.lower()
    assert "research/PIPELINE_STATE.json" not in preflight.missing_artifacts
    assert "create only these missing" in preflight.bootstrap_objective
    assert "re-check it at execution time" in preflight.bootstrap_objective
    assert "target_venue" in preflight.bootstrap_objective
    assert "research/EXPERIMENT_PLAN.md" in preflight.missing_artifacts
    assert "`research/EXPERIMENT_PLAN.md`" in preflight.bootstrap_objective
    assert "research bootstrap requested" in preflight.event_text
    assert "missing research artifacts" in preflight.event_text


def test_explicit_research_profile_repairs_code_bearing_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "existing-code"
    source = repo_dir / "src" / "package" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("", encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is True
    assert "research bootstrap mission" in preflight.bootstrap_objective.lower()
    assert preflight.event_text.startswith("research scaffold incomplete")
    assert "uninitialized project root" not in preflight.event_text


def test_memory_runner_repairs_research_after_starter_code_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.apps._runtime_backends import _MemoryRunner

    source = tmp_path / "code" / "experiment_io.py"
    source.parent.mkdir(parents=True)
    source.write_text("# seeded before backlog execution\n", encoding="utf-8")
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "quant")
    runner = _MemoryRunner()
    runner.workdir = tmp_path

    runner._materialize_bootstrap_skeleton("research objective")

    assert (tmp_path / "research" / "PIPELINE_STATE.json").exists()
    assert (tmp_path / "research" / "RESEARCH_BRIEF.md").exists()
    assert (tmp_path / "experiments" / "BENCHMARK_PROVENANCE.md").exists()


def test_inspect_project_bootstrap_treats_research_seed_as_initialized(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    for rel_path in (
        "research/PIPELINE_STATE.json",
        "research/RESEARCH_BRIEF.md",
        "research/EXPERIMENT_PLAN.md",
        "research/CLAIMS_TO_TEST.md",
        "research/GO_NO_GO.md",
        "experiments/BENCHMARK_PROVENANCE.md",
    ):
        path = repo_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("seed\n", encoding="utf-8")

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is False
    assert preflight.bootstrap_objective == ""
    assert preflight.event_text == ""


def test_inspect_project_bootstrap_leaves_complete_research_seed_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    for rel_path in (
        "research/PIPELINE_STATE.json",
        "research/RESEARCH_BRIEF.md",
        "research/EXPERIMENT_PLAN.md",
        "research/CLAIMS_TO_TEST.md",
        "research/GO_NO_GO.md",
        "experiments/BENCHMARK_PROVENANCE.md",
    ):
        path = repo_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("seed\n", encoding="utf-8")

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is False
    assert preflight.bootstrap_objective == ""
    assert preflight.event_text == ""


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


def test_read_daemon_status_parses_budget_caps(tmp_path: Path) -> None:
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
                    "per_mission_cap_usd": 12.5,
                    "daily_cap_usd": 42.25,
                    "global_daily_cap_usd": 84.5,
                }
            ),
            encoding="utf-8",
        )
        s = read_daemon_status(tmp_path)
    assert s.alive is True
    assert s.per_mission_cap_usd == 12.5
    assert s.daily_cap_usd == 42.25
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
                    "per_mission_cap_usd": 999,
                }
            ),
            encoding="utf-8",
        )
        status = read_daemon_status(tmp_path)

    assert status.alive is True
    assert status.pid == lock.pid
    assert status.backend is None
    assert status.per_mission_cap_usd is None
    assert "does not match lock pid" in status.status_read_error


def test_resolve_effective_budget_migrates_env_once_when_daemon_is_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "global"))
    monkeypatch.setenv("ARGUS_SKILL_PER_MISSION_CAP_USD", "7.5")
    monkeypatch.setenv("ARGUS_SKILL_DAILY_CAP_USD", "19.25")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "55.0")
    status = DaemonStatus(
        alive=False,
        pid=1234,
        started_at_iso=None,
        uptime_seconds=None,
        life_dir=tmp_path,
        backend="memory",
        per_mission_cap_usd=99.0,
        daily_cap_usd=88.0,
        global_daily_cap_usd=77.0,
    )

    budget = resolve_effective_budget(status)

    assert budget.per_mission_cap_usd == 7.5
    assert budget.daily_cap_usd == 19.25
    assert budget.global_daily_cap_usd == 55.0

    monkeypatch.setenv("ARGUS_SKILL_PER_MISSION_CAP_USD", "999")
    monkeypatch.setenv("ARGUS_SKILL_DAILY_CAP_USD", "999")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "999")
    restarted = resolve_effective_budget(status)

    assert restarted == budget


def test_life_worker_start_does_not_rewrite_existing_budget_files(
    tmp_path: Path,
) -> None:
    from argus_skill.core.project_budget import (
        GlobalBudget,
        ProjectBudget,
        global_budget_path,
        write_global_budget,
        write_project_budget,
    )

    project = tmp_path / "projects" / "demo"
    write_project_budget(project, ProjectBudget(7, 17))
    write_global_budget(tmp_path, GlobalBudget(27))
    project_path = project / "budget.json"
    global_path = global_budget_path(tmp_path)
    before = {
        project_path: (project_path.read_bytes(), project_path.stat().st_mtime_ns),
        global_path: (global_path.read_bytes(), global_path.stat().st_mtime_ns),
    }

    config = LifeWorkerConfig(
        life_dir=project,
        global_root=tmp_path,
        backend="memory",
        per_mission_cap_usd=999,
        daily_cap_usd=999,
        global_daily_cap_usd=999,
    )
    LifeWorker(config)

    assert config.per_mission_cap_usd == 7
    assert config.daily_cap_usd == 17
    assert config.global_daily_cap_usd == 27
    for path, (content, mtime_ns) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime_ns


def test_stop_daemon_returns_1_when_no_daemon(tmp_path: Path) -> None:
    assert stop_daemon(tmp_path) == 1


def test_clean_spawn_execs_helper_without_inheriting_parent_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir()
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
    assert captured["cwd"] == str(workdir)
    payload = json.loads(captured["input"])
    assert payload["life_dir"] == str(config.life_dir)
    assert payload["continuous_objective"] == "continue research"


def test_stop_daemon_does_not_sigkill_after_pid_identity_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.daemon import state as daemon_state

    statuses = iter(
        [
            DaemonStatus(True, 123, None, None, tmp_path),
            DaemonStatus(True, 123, None, None, tmp_path),
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
    assert signals == [(123, daemon_state.signal.SIGTERM)]


def _spawn_fake_daemon(tmp_path: Path, pre_ready: str, post_ready: str) -> int:
    """Spawn a DETACHED fake daemon (double-fork, reparented to init) and return
    its pid.

    Detaching mirrors the real daemon (spawn_detached_daemon) and — crucially —
    means the exited process is reaped by init, not left a zombie under the test
    process. A zombie still answers ``os.kill(pid, 0)``, which would make
    stop_daemon's liveness check wrongly report 'still alive'. ``pre_ready``
    installs SIGTERM handling before the ready-marker is touched.
    """
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
            "_stop = []\n"
            "def _h(*a):\n"
            "    _stop.append(1)\n"
            "signal.signal(signal.SIGTERM, _h)\n"
        ),
        post_ready=(
            "while not _stop:\n"
            "    time.sleep(0.05)\n"
            "time.sleep(0.8)\n"
            "os._exit(0)\n"
        ),
    )
    try:
        life_worker_mod.write_continuous_config(
            tmp_path, enabled=True, objective="keep going"
        )
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
def test_life_worker_drains_successive_missions_and_stops_on_signal(
    tmp_path: Path,
) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path, backend="memory",
        per_mission_cap_usd=10.0, daily_cap_usd=100.0, poll_interval=0.1,
    )
    mem = LifeMemory.open(tmp_path)
    mem.init()
    first = BacklogItem.new(title="first", objective="say first", max_cost_usd=1.0)
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
        max_cost_usd=1.0,
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
    from argus_skill.apps.cli import _format_short_duration
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

    expected_path = (
        tmp_path / expected / "skills"
        if skills_env is None
        else tmp_path / expected
    )
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
    error = _workspace_start_error(LifeWorkerConfig(
        life_dir=target_life,
        global_root=root,
        project_workdir=workdir,
        project_fingerprint="s-target",
    ))

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

    error = _workspace_start_error(LifeWorkerConfig(
        life_dir=life_dir,
        global_root=root,
        project_workdir=old,
        project_fingerprint="s-target",
    ))

    assert "workdir changed during daemon startup" in error


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
        per_mission_cap_usd=1.5,
        daily_cap_usd=9.5,
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


def test_handoff_config_preserves_explicit_zero_budget_caps(tmp_path: Path) -> None:
    cfg = LifeWorkerConfig(
        life_dir=tmp_path / "project",
        per_mission_cap_usd=0.0,
        daily_cap_usd=0.0,
        global_daily_cap_usd=0.0,
        planner_task_iteration_budget_usd=0.0,
    )

    restored = _config_from_payload(_config_payload(cfg))

    assert restored.per_mission_cap_usd == 0.0
    assert restored.daily_cap_usd == 0.0
    assert restored.global_daily_cap_usd == 0.0
    assert restored.planner_task_iteration_budget_usd == 0.0


def test_active_daemon_count_resolves_default_global_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(life_worker_mod.core_paths, "global_root", lambda: tmp_path)
    (tmp_path / "projects").mkdir()

    assert life_worker_mod._active_daemon_count(
        LifeWorkerConfig(life_dir=tmp_path / "project", global_root=None)
    ) == 0


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


def test_life_worker_handoff_stops_after_planner_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signatures = iter(["old", "new"])
    spawned: dict[str, object] = {}

    monkeypatch.setenv("ARGUS_SKILL_DAEMON_AUTO_RESTART", "1")
    monkeypatch.delenv(life_worker_mod._SOURCE_SIGNATURE_ENV, raising=False)
    monkeypatch.setattr(life_worker_mod, "_source_signature", lambda: next(signatures))

    def _fake_spawn(
        config: LifeWorkerConfig,
        *,
        source_signature: str,
        reason: str,
    ) -> bool:
        spawned["config"] = config
        spawned["source_signature"] = source_signature
        spawned["reason"] = reason
        return True

    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", _fake_spawn)

    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")
    worker = LifeWorker(cfg)

    assert worker._planner_restart_handler("planner says restart") is True
    assert worker._stop.is_set()
    assert spawned == {
        "config": cfg,
        "source_signature": "new",
        "reason": "planner says restart",
    }


def test_life_worker_planner_runtime_context_reports_source_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signatures = iter(["old", "new"])

    monkeypatch.setenv("ARGUS_SKILL_DAEMON_AUTO_RESTART", "1")
    monkeypatch.setattr(life_worker_mod, "_source_signature", lambda: next(signatures))

    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))

    context = worker._planner_runtime_context()

    assert "Runtime source changed" in context
    assert "restart_daemon=true" in context
    assert not worker._stop.is_set()


def test_life_worker_post_mission_hook_handoffs_on_source_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signatures = iter(["old", "new"])
    spawned: dict[str, object] = {}

    monkeypatch.setenv("ARGUS_SKILL_DAEMON_AUTO_RESTART", "1")
    monkeypatch.delenv(life_worker_mod._SOURCE_SIGNATURE_ENV, raising=False)
    monkeypatch.setattr(life_worker_mod, "_source_signature", lambda: next(signatures))

    def _fake_spawn(
        config: LifeWorkerConfig,
        *,
        source_signature: str,
        reason: str,
    ) -> bool:
        spawned["config"] = config
        spawned["source_signature"] = source_signature
        spawned["reason"] = reason
        return True

    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", _fake_spawn)

    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")
    worker = LifeWorker(cfg)

    assert worker._post_mission_hook({"status": "done"}) == "daemon_handoff"
    assert worker._stop.is_set()
    assert spawned == {
        "config": cfg,
        "source_signature": "new",
        "reason": (
            "runtime source changed after mission completion; "
            "blue/green reload needed for self-architecture update"
        ),
    }


def test_life_worker_post_mission_hook_noops_without_source_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_called = False

    monkeypatch.setenv("ARGUS_SKILL_DAEMON_AUTO_RESTART", "1")
    monkeypatch.delenv(life_worker_mod._SOURCE_SIGNATURE_ENV, raising=False)
    monkeypatch.setattr(life_worker_mod, "_source_signature", lambda: "same")

    def _fake_spawn(*_args: object, **_kwargs: object) -> bool:
        nonlocal spawn_called
        spawn_called = True
        return True

    monkeypatch.setattr(life_worker_mod, "_spawn_handoff_candidate", _fake_spawn)

    worker = LifeWorker(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))

    assert worker._post_mission_hook({"status": "done"}) == ""
    assert not worker._stop.is_set()
    assert spawn_called is False


def test_worker_runtime_context_includes_research_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    cfg = LifeWorkerConfig(
        life_dir=tmp_path,
        backend="codex",
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4-mini",
        per_mission_cap_usd=5.0,
        daily_cap_usd=20.0,
    )

    context = _worker_runtime_context(cfg)

    assert "Runtime info" in context
    assert "Engineer model: gpt-5.4-mini" in context
    assert "profile_name: emnlp2026-tierharness" in context
    assert "profile_sha256:" in context


def test_worker_runtime_context_omits_research_profile_for_bounded_vertical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    monkeypatch.setenv(
        "ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(tmp_path / "no_special_prompts")
    )
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    assert _worker_runtime_context(cfg, paper_mission=False) == ""


def test_worker_runtime_context_empty_without_research_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    # Isolate operator special prompts so the host's directives don't leak in.
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR",
                       str(tmp_path / "no_special_prompts"))
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
    (sp / "10-gpu.md").write_text("Free the keep-alive before training.",
                                  encoding="utf-8")
    (sp / "10-gpu.md").chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(sp))
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    context = _worker_runtime_context(cfg)
    assert "Operator Directives" in context
    assert "Free the keep-alive before training." in context


def test_handoff_source_signature_reads_test_override_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signature_path = tmp_path / "signature.txt"
    signature_path.write_text("alpha\n", encoding="utf-8")
    monkeypatch.setenv(
        life_worker_mod._TEST_SOURCE_SIGNATURE_FILE_ENV,
        str(signature_path),
    )

    assert life_worker_mod._source_signature() == "alpha"

    signature_path.write_text("beta\n", encoding="utf-8")

    assert life_worker_mod._source_signature() == "beta"


def test_source_signature_includes_builtin_skill_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A skill edit changes engineer/reviewer behavior, so the daemon staleness
    # signature must cover builtin_skills/**/*.md, not just *.py + pyproject.
    monkeypatch.delenv(
        life_worker_mod._TEST_SOURCE_SIGNATURE_FILE_ENV, raising=False)
    pkg_root = Path(life_worker_mod.__file__).resolve().parents[1]
    tmp_skill = pkg_root / "builtin_skills" / "engineer" / "_sigtest_tmp_skill.md"
    baseline = life_worker_mod._source_signature()
    assert len(baseline) == 64  # sha256 hex digest
    tmp_skill.write_text(
        "---\nname: sigtest\n---\n# sigtest\nbody\n", encoding="utf-8")
    try:
        changed = life_worker_mod._source_signature()
    finally:
        tmp_skill.unlink()
    assert changed != baseline
    assert life_worker_mod._source_signature() == baseline


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

    assert compare_and_swap_continuous_config(
        tmp_path,
        expected=expected,
        enabled=True,
        objective="cleaned older objective",
    ) is False
    assert read_continuous_state(tmp_path) == ContinuousConfigState(
        enabled=False,
        objective="newer objective",
    )


def test_continuous_config_cas_detects_same_value_command(tmp_path: Path) -> None:
    from argus_skill.daemon.state import compare_and_swap_continuous_config

    write_continuous_config(tmp_path, enabled=False, objective="paused objective")
    expected = read_continuous_state(tmp_path)
    write_continuous_config(tmp_path, enabled=False, objective="paused objective")

    assert compare_and_swap_continuous_config(
        tmp_path,
        expected=expected,
        enabled=True,
        objective="clean objective",
    ) is False
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
                enabled, objective = self.config.continuous_config_provider()
                self.config.continuous = enabled
                if objective:
                    self.config.continuous_objective = objective
            seen["continuous"].append(
                (self.config.continuous, self.config.continuous_objective)
            )
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
    LifeMemory.open(tmp_path).init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="manager-clean execution objective",
    )
    from argus_skill.skills.vertical_select import persist_vertical

    persist_vertical(tmp_path, "research")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "life.manager.intent.completed",
            "intent_id": "intent-original",
            "continuous_generation": 1,
            "execution_task": "manager-clean execution objective",
            "vertical": "research",
        }) + "\n")
    (tmp_path / "manager-handoff.json").write_text(
        json.dumps({
            "version": 1,
            "objective_sha256": "stale",
            "vertical": "research",
            "continuous_generation": 0,
            "intent_id": "intent-stale",
        }),
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
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
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
        fh.write(json.dumps({
            "type": "life.manager.intent.completed",
            "intent_id": "intent-old",
            "continuous_generation": 1,
            "execution_task": "old execution objective",
            "vertical": "research",
        }) + "\n")
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
        )

    monkeypatch.setattr(
        "argus_skill.manager.Manager.decide_vertical",
        decide_vertical,
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
    seen: dict[str, object] = {}

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            seen["objective"] = self.config.continuous_objective
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        continuous=True,
        continuous_objective="new raw objective",
        resume_continuous=True,
    ))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert calls == ["new raw objective"]
    assert seen["objective"] == "new manager-clean objective"
    assert read_continuous_state(tmp_path).objective == "new manager-clean objective"


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
                enabled, objective = self.config.continuous_config_provider()
                self.config.continuous = enabled
                if objective:
                    self.config.continuous_objective = objective
            seen["continuous"].append(
                (self.config.continuous, self.config.continuous_objective)
            )
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
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert seen == {"continuous": False, "objective": "newer objective"}
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
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
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
            enabled, objective = self.config.continuous_config_provider()
            seen.update(enabled=enabled, objective=objective)
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    assert seen == {"enabled": False, "objective": raw}
    assert read_continuous_state(tmp_path).enabled is True


def test_daemon_manager_decision_failure_preserves_persisted_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("backend unavailable")
        ),
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
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    after = read_continuous_state(tmp_path)
    assert seen == {"enabled": False, "objective": raw}
    assert after.enabled is True
    assert after.objective == raw
    assert after.generation == before.generation


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

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {"stopped_by": "backlog_empty"}

    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    after = read_continuous_state(tmp_path)
    assert after.enabled is False
    assert after.objective == "paused objective"
    assert after.generation == before.generation


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
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    assert worker.run_forever() == 0
    state = read_continuous_state(tmp_path)
    assert state.enabled is True
    assert state.objective == "objective"


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
    worker = LifeWorker(LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        poll_interval=0.01,
        resume_continuous=True,
    ))

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

    assert life_worker_mod.continuous_mode_error(
        "memory",
        True,
        "keep going",
    ) == ""


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
