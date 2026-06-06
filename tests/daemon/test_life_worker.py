"""Smoke tests for the 7×24 life worker."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import argus_skill.daemon.life_worker as life_worker_mod
from argus_skill.core.bootstrap import inspect_project_bootstrap
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
    read_continuous_state,
    read_daemon_status,
    resolve_effective_budget,
    stop_daemon,
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
    "ARGUS_SKILL_HOME",
    "ARGUS_SKILL_LIFE_BACKEND",
    "ARGUS_SKILL_MAX_ROUNDS",
    "ARGUS_SKILL_PER_MISSION_CAP_USD",
    "ARGUS_SKILL_PLAN_MODE",
    "ARGUS_SKILL_PLAN_MODEL",
    "ARGUS_SKILL_RESEARCH_PROFILE",
    "ARGUS_SKILL_RESEARCH_PROFILE_PATH",
    "ARGUS_SKILL_REVIEWER_MODEL",
    "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "ARGUS_SKILL_SCIENTIST_MODEL",
    "ARGUS_SKILL_SCIENTIST_REASONING_EFFORT",
    "ARGUS_SKILL_SKILLS_DIR",
    "ARGUS_SKILL_TELEGRAM_BOT_TOKEN",
    "ARGUS_SKILL_TELEGRAM_CHAT_ID",
    "ARGUS_SKILL_TELEGRAM_USER_ID",
    "ARGUS_SKILL_WORKDIR",
)


@pytest.fixture(autouse=True)
def _clear_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)


def test_read_daemon_status_returns_not_alive_on_missing_pid(tmp_path: Path) -> None:
    status = read_daemon_status(tmp_path)
    assert isinstance(status, DaemonStatus)
    assert status.alive is False
    assert status.pid is None
    assert status.life_dir == tmp_path


def test_inspect_project_bootstrap_detects_empty_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()

    preflight = inspect_project_bootstrap(repo_dir)

    assert preflight.should_bootstrap is True
    assert preflight.missing_artifacts == (".git", "build manifest", "README*", "source files")
    assert "pyproject.toml" in preflight.bootstrap_objective
    assert "README.md" in preflight.bootstrap_objective
    assert f"src/{repo_dir.name.replace('-', '_')}/__init__.py" in preflight.bootstrap_objective
    assert "uninitialized project root" in preflight.event_text


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
    # structured research profile, a research-sounding objective falls back to
    # the GENERIC bootstrap — the agent decides the science, not the harness.
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    repo_dir = tmp_path / "empty-repo"
    repo_dir.mkdir()

    preflight = inspect_project_bootstrap(
        repo_dir,
        objective_hint="EMNLP auto-research bootstrap mission",
    )

    assert preflight.should_bootstrap is True
    # Generic bootstrap, NOT the research seed.
    assert "pyproject.toml" in preflight.bootstrap_objective
    assert "research bootstrap mission" not in preflight.bootstrap_objective.lower()
    assert "research bootstrap requested" not in preflight.event_text


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
    assert "research/EXPERIMENT_PLAN.md" in preflight.missing_artifacts
    assert "research bootstrap requested" in preflight.event_text
    assert "missing research artifacts" in preflight.event_text


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


def test_read_daemon_status_treats_garbage_pid_file_as_dead(tmp_path: Path) -> None:
    (tmp_path / "daemon.pid").write_text("not-a-number\n")
    s = read_daemon_status(tmp_path)
    assert s.alive is False and s.pid is None


def test_read_daemon_status_parses_budget_caps(tmp_path: Path) -> None:
    pid = os.getpid()
    (tmp_path / "daemon.pid").write_text(f"{pid}\n")
    (tmp_path / "daemon.status.json").write_text(
        json.dumps(
            {
                "pid": pid,
                "started_at_iso": "2024-01-01T00:00:00+00:00",
                "backend": "memory",
                "life_dir": str(tmp_path),
                "per_mission_cap_usd": 12.5,
                "daily_cap_usd": 42.25,
            }
        ),
        encoding="utf-8",
    )
    s = read_daemon_status(tmp_path)
    assert s.alive is True
    assert s.per_mission_cap_usd == 12.5
    assert s.daily_cap_usd == 42.25


def test_resolve_effective_budget_falls_back_to_env_when_daemon_is_down(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PER_MISSION_CAP_USD", "7.5")
    monkeypatch.setenv("ARGUS_SKILL_DAILY_CAP_USD", "19.25")
    status = DaemonStatus(
        alive=False,
        pid=1234,
        started_at_iso=None,
        uptime_seconds=None,
        life_dir=tmp_path,
        backend="memory",
        per_mission_cap_usd=99.0,
        daily_cap_usd=88.0,
    )

    budget = resolve_effective_budget(status)

    assert budget.per_mission_cap_usd == 7.5
    assert budget.daily_cap_usd == 19.25


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
    _wait_for_thread_stop(t, timeout=10.0)


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
    _wait_for_thread_stop(t, timeout=10.0)


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


def test_life_worker_wires_stop_event_to_codex_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = LifeWorkerConfig(life_dir=tmp_path, backend="codex", poll_interval=0.1)
    LifeMemory.open(tmp_path).init()
    captured: dict[str, Any] = {}

    def fake_build_life_runner(ns: Any) -> object:
        captured["stop_event"] = getattr(ns, "stop_event", None)
        return object()

    class FakeSupervisor:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.config: Any = kwargs["config"]

        def run(self) -> dict[str, Any]:
            self.config.stop_event.set()
            return {}

    monkeypatch.setattr(
        "argus_skill.apps._life_repl.build_life_runner",
        fake_build_life_runner,
    )
    monkeypatch.setattr("argus_skill.daemon.life_worker.LifeSupervisor", FakeSupervisor)

    worker = LifeWorker(cfg)
    worker._install_signal_handlers = lambda: None  # type: ignore[method-assign]

    rc = worker.run_forever()

    assert captured["stop_event"] is worker._stop
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
    assert ns.engineer_reasoning_effort == "high"
    assert ns.reviewer_reasoning_effort == "high"
    assert ns.scientist_reasoning_effort == "high"


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
        scientist_model="sci",
        per_mission_cap_usd=1.5,
        daily_cap_usd=9.5,
        poll_interval=0.25,
        log_path=tmp_path / "daemon.log",
        continuous=True,
        continuous_objective="keep improving",
        continuous_open_ended=False,
    )

    restored = _config_from_payload(_config_payload(cfg))

    assert restored == cfg
    assert restored.continuous_open_ended is False


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
        LifeWorkerConfig(life_dir=tmp_path, backend="memory", poll_interval=0.01)
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
