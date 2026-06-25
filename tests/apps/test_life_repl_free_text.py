"""Tests for the Manager REPL helpers in ``argus_skill.manager.repl``.

Conversation-surface helpers live in ``argus_skill.manager.repl``; the
runtime infrastructure they drive (runner factory, supervisor driver,
``_SkillLoopRunner``) lives in ``argus_skill.apps._runtime``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

import argus_skill.adapters.agent_cli_backend as agent_cli_backend_mod
from argus_skill.apps import _runtime
from argus_skill.daemon.life_worker import write_continuous_config
from argus_skill.life import MemoryBundle
from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.manager import repl as manager_repl

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
    "ARGUS_SKILL_AUTHOR_MODEL",
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


@pytest.fixture()
def mem(tmp_path: Path) -> LifeMemory:
    return LifeMemory.open(root=tmp_path)


@pytest.mark.parametrize(
    ("skills_env", "expected"),
    [
        (None, "root/skills"),
        ("custom-skills", "custom-skills"),
    ],
)
def test_invoke_supervisor_uses_global_skills_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    skills_env: str | None,
    expected: str,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "root"))
    monkeypatch.delenv("ARGUS_SKILL_SKILLS_DIR", raising=False)
    if skills_env is not None:
        monkeypatch.setenv("ARGUS_SKILL_SKILLS_DIR", str(tmp_path / skills_env))

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    bundle = MemoryBundle.for_cwd(repo)

    captured: dict[str, Any] = {}

    class DummyRunner:
        backend: Any = None
        last_thread_id: str | None = None

    def fake_build_life_runner(ns: argparse.Namespace, *, seed_thread_id=None):
        captured["skills_dir"] = ns.skills_dir
        return DummyRunner()

    def fake_run_life_supervisor(**kwargs: Any) -> dict[str, Any]:
        captured["runtime_context"] = kwargs["runtime_context"]
        captured["project_worktree"] = kwargs["project_worktree"]
        return {"missions_run": 0}

    monkeypatch.setattr(_runtime, "build_life_runner", fake_build_life_runner)
    monkeypatch.setattr(_runtime, "run_life_supervisor", fake_run_life_supervisor)

    summary, last_thread_id = _runtime._invoke_supervisor(
        mem=bundle,
        backend="memory",
        once=True,
        max_missions=1,
        per_mission_cap_usd=1.0,
        daily_cap_usd=1.0,
    )

    expected_path = (
        tmp_path / "root" / "skills"
        if skills_env is None
        else tmp_path / expected
    )
    assert captured["skills_dir"] == str(expected_path)
    assert captured["project_worktree"] == repo
    assert "- Engineer reasoning effort: high" in captured["runtime_context"]
    assert "- Reviewer reasoning effort: high" in captured["runtime_context"]
    assert summary == {"missions_run": 0}
    assert last_thread_id is None


def test_invoke_supervisor_injects_research_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "root"))
    monkeypatch.setenv("ARGUS_SKILL_RESEARCH_PROFILE", "emnlp2026-tierharness")
    monkeypatch.delenv("ARGUS_SKILL_RESEARCH_PROFILE_PATH", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("supervisor smoke\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    bundle = MemoryBundle.for_cwd(repo)
    captured: dict[str, Any] = {}

    class DummyRunner:
        backend: Any = None
        last_thread_id: str | None = None

    monkeypatch.setattr(
        _runtime,
        "build_life_runner",
        lambda ns, *, seed_thread_id=None: DummyRunner(),
    )

    def fake_run_life_supervisor(**kwargs: Any) -> dict[str, Any]:
        captured["runtime_context"] = kwargs["runtime_context"]
        captured["project_worktree"] = kwargs["project_worktree"]
        return {"missions_run": 0}

    monkeypatch.setattr(_runtime, "run_life_supervisor", fake_run_life_supervisor)

    _runtime._invoke_supervisor(
        mem=bundle,
        backend="memory",
        once=True,
        max_missions=1,
        per_mission_cap_usd=1.0,
        daily_cap_usd=1.0,
    )

    assert "Runtime info" in captured["runtime_context"]
    assert "profile_name: emnlp2026-tierharness" in captured["runtime_context"]
    assert "Profile metadata:" in captured["runtime_context"]
    assert captured["project_worktree"] == repo


def test_codex_skill_loop_runner_strips_legacy_auto_max_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeAgentCliBackend:
        def __init__(
            self,
            *,
            backend: str | None = None,
            runner_bin: str | None = None,
            default_extra_args: list[str] | None = None,
            default_interrupt_reason_provider: Any | None = None,
            default_watchdog_soft_idle_seconds: int = 0,
            default_watchdog_hard_idle_seconds: int = 0,
            event_callback: Any | None = None,
        ) -> None:
            captured["backend"] = backend
            captured["runner_bin"] = runner_bin
            captured["default_extra_args"] = default_extra_args
            captured["soft_idle"] = default_watchdog_soft_idle_seconds
            captured["hard_idle"] = default_watchdog_hard_idle_seconds
            captured["event_callback"] = event_callback
            self.backend = backend

    monkeypatch.setattr(agent_cli_backend_mod, "AgentCliBackend", FakeAgentCliBackend)
    monkeypatch.setenv(
        "ARGUS_SKILL_RUNNER_EXTRA_ARGS",
        '-c "profile = \\"auto-max\\"" --trace',
    )
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)

    runner = _runtime._SkillLoopRunner(
        argparse.Namespace(stop_event=None),
        seed_thread_id=None,
    )

    assert captured["default_extra_args"] == ["--trace"]
    assert runner.backend is runner._backend


def test_invoke_and_track_clears_stale_thread_id_on_poisoned_outcome(
    mem: LifeMemory,
) -> None:
    chat_state: dict[str, Any] = {
        "backend": "memory",
        "theme": None,
        "last_thread_id": "stale-thread",
        "last_elapsed_s": None,
        "total_elapsed_s": 0.0,
        "mission_count": 0,
    }

    with patch.object(
        manager_repl,
        "_invoke_supervisor",
        return_value=({"missions_run": 1}, None),
    ):
        manager_repl._invoke_and_track(
            mem=cast(Any, mem),
            chat_state=chat_state,
            once=True,
            max_missions=1,
            per_mission_cap_usd=1.0,
            daily_cap_usd=1.0,
            quiet=True,
        )

    assert chat_state["last_thread_id"] is None
    assert chat_state["mission_count"] == 1
    assert chat_state["last_elapsed_s"] is not None


def test_add_only_default_priority(mem: LifeMemory, capsys: pytest.CaptureFixture[str]) -> None:
    item = manager_repl._add_only(mem, "do the dishes")
    assert item.priority == 100
    assert item.max_cost_usd == 30.0
    assert item.iteration_max_cycles == 6
    assert item.iteration_budget_usd == 30.0
    head = mem.backlog.next_pending()
    assert head is not None
    assert head.id == item.id
    out = capsys.readouterr().out
    assert "do the dishes" in out


def test_free_text_runs_just_typed_objective_not_older_pending(
    mem: LifeMemory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: typing ``hello`` at the prompt must execute ``hello``,
    not whatever stale pending item was at the head of the backlog."""
    older = mem.backlog.add(BacklogItem.new(
        title="old work",
        objective="finish the base64 helper",
        priority=100,
    ))

    captured: dict[str, Any] = {}

    def fake_invoke(**kwargs: Any) -> dict[str, Any]:
        head = kwargs["mem"].backlog.next_pending()
        assert head is not None
        captured["head_id"] = head.id if head else None
        captured["head_obj"] = head.objective if head else None
        return {"missions_run": 1, "total_cost_usd": 0.0}

    with patch.object(manager_repl, "_invoke_and_track", side_effect=fake_invoke):
        manager_repl._free_text_cmd(mem, "你好", chat_state={"backend": "memory"})

    pending = mem.backlog.pending()
    assert pending, "free-text input must enqueue an item"
    assert pending[0].objective == "你好", (
        "free-text item must be at head of pending queue, "
        f"got: {[(it.priority, it.objective) for it in pending]}"
    )
    assert pending[0].priority < older.priority
    assert captured["head_obj"] == "你好"


def test_free_text_beats_aggressive_priority_zero_pending(mem: LifeMemory) -> None:
    """Even if a queued ``/add`` item has priority 0, free text still wins."""
    mem.backlog.add(BacklogItem.new(title="crit", objective="critical", priority=0))

    captured: dict[str, Any] = {}

    def fake_invoke(**kwargs: Any) -> dict[str, Any]:
        head = kwargs["mem"].backlog.next_pending()
        assert head is not None
        captured["head_obj"] = head.objective if head else None
        return {"missions_run": 1, "total_cost_usd": 0.0}

    with patch.object(manager_repl, "_invoke_and_track", side_effect=fake_invoke):
        manager_repl._free_text_cmd(mem, "right now please", chat_state={"backend": "memory"})

    assert captured["head_obj"] == "right now please"


def test_repl_help_matches_documented_command_surface(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    for name in _ENV_VARS_TO_CLEAR:
        env.pop(name, None)
    env["ARGUS_SKILL_LIFE_BACKEND"] = "memory"

    # The lifetime entry gate refuses to start unless an objective AND at least
    # one trusted special prompt are configured. Satisfy both for this surface
    # test: persist an objective at the project root the gate resolves, and seed
    # a chmod-0644 directive (0664 would be rejected as group-writable).
    from argus_skill.apps._target_paths import resolve_life_root
    from argus_skill.life import MemoryBundle

    bundle = MemoryBundle.for_cwd(repo, global_root=resolve_life_root(str(tmp_path)))
    bundle.init()
    write_continuous_config(
        bundle.project.root, enabled=True, objective="keep the cockpit warm"
    )
    sp = tmp_path / "special_prompts"
    sp.mkdir()
    rule = sp / "10-house-rules.md"
    rule.write_text("Operational house rules for this box.\n", encoding="utf-8")
    rule.chmod(0o644)
    env["ARGUS_SKILL_SPECIAL_PROMPTS_DIR"] = str(sp)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "argus_skill",
            "--no-daemon",
            "--life-dir",
            str(tmp_path),
        ],
        cwd=repo,
        env=env,
        input="/help\n/exit\n",
        text=True,
        capture_output=True,
        timeout=120,
        check=True,
    )
    out = result.stdout + result.stderr
    for fragment in (
        "/help",
        "/status",
        "/config [key=val ...]",
        "/identity [edit|set",
        "/project [set",
        "/backlog [all]",
        "/add <text> [--once] [--cycles=N] [--budget=$X]",
        "/done|/skip|/rm <id>",
        "/stop <id>",
        "/journal [N]",
        "/note <text>",
        "/nudge <text>",
        "/run [opts]",
        "/skills [ls|promote <name>]",
        "/reset",
        "/backend",
        "/exit  /quit  :q",
    ):
        assert fragment in out
    assert "/correct" not in out


def test_project_cmd_reads_and_updates_project_card(
    mem: LifeMemory, capsys: pytest.CaptureFixture[str]
) -> None:
    mem.identity.path.write_text("identity: initial\n", encoding="utf-8")
    (mem.root / "project.md").write_text("project: initial\n", encoding="utf-8")

    manager_repl._project_cmd(mem, [], "")
    out = capsys.readouterr().out
    assert "project: initial" in out

    manager_repl._project_cmd(mem, ["set"], "set project: updated")
    out = capsys.readouterr().out
    assert "project card updated" in out
    assert (mem.root / "project.md").read_text(encoding="utf-8") == "project: updated\n"
    assert mem.identity.read() == "identity: initial\n"


def test_repl_startup_preserves_custom_project_card_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    bundle = MemoryBundle.for_cwd(repo)
    custom = "# custom\n\n## Project label\n- keep me\n"
    bundle.project.project_card.path.parent.mkdir(parents=True, exist_ok=True)
    bundle.project.project_card.path.write_text(custom, encoding="utf-8")

    created = bundle.init()

    assert created["project"]["project_card"] is False
    assert bundle.project.project_card.path.read_text(encoding="utf-8") == custom


# ---------------------------------------------------------------------------
# Singleton lock
# ---------------------------------------------------------------------------

def test_run_manager_repl_refuses_concurrent_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second REPL launched while the first holds the lock must
    print a clear error and exit non-zero, NOT silently corrupt
    backlog.jsonl by racing on rewrites."""
    import argparse

    from argus_skill.core.daemon_lock import acquire_global_daemon_lock

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    project_root = MemoryBundle.for_cwd(repo, global_root=life_dir).project.root

    # Simulate the lock being held by a "first" REPL process.
    # acquire_global_daemon_lock is per-pid_path, so use the same path
    # the REPL would use: <project-root>/repl.pid.
    lock = acquire_global_daemon_lock(pid_path=project_root / "repl.pid")
    try:
        ns = argparse.Namespace(life_dir=str(life_dir), color="never", verbose=None)
        rc = manager_repl.run_manager_repl(ns)
    finally:
        lock.release()

    assert rc == 2
    captured = capsys.readouterr()
    err = captured.err + captured.out
    assert "another REPL is already running" in err


def test_run_manager_repl_releases_lock_on_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the REPL exits, a second invocation must be able to
    acquire the lock — i.e. release was actually called."""
    import argparse
    from unittest.mock import patch

    from argus_skill.core.daemon_lock import (
        DaemonAlreadyRunning,
        acquire_global_daemon_lock,
    )

    life_dir = tmp_path / "life"
    life_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    project_root = MemoryBundle.for_cwd(repo, global_root=life_dir).project.root

    # Patch the inner loop to be a no-op so we just exercise lock+release.
    with patch.object(manager_repl, "_run_manager_repl_locked", return_value=0):
        ns = argparse.Namespace(life_dir=str(life_dir), color="never", verbose=None)
        rc = manager_repl.run_manager_repl(ns)
    assert rc == 0

    # The lock must be reacquirable now.
    lock = acquire_global_daemon_lock(pid_path=project_root / "repl.pid")
    try:
        # And taking it again would fail.
        with pytest.raises(DaemonAlreadyRunning):
            acquire_global_daemon_lock(pid_path=project_root / "repl.pid")
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# _parse_add_flags with session defaults
# ---------------------------------------------------------------------------

def test_parse_add_flags_uses_session_defaults() -> None:
    """When no inline flags are given, the session config defaults are
    used rather than the hardcoded function defaults."""
    iterate, cycles, budget, body = manager_repl._parse_add_flags(
        "hello world",
        default_iterate=False,
        default_cycles=10,
        default_budget=100.0,
    )
    assert body == "hello world"
    assert iterate is False
    assert cycles == 10
    assert budget == 100.0


def test_parse_add_flags_inline_overrides_session_defaults() -> None:
    """Inline ``--once`` and ``--cycles=3`` must override session defaults."""
    iterate, cycles, budget, body = manager_repl._parse_add_flags(
        "--once --cycles=3 do some work",
        default_iterate=True,
        default_cycles=10,
        default_budget=100.0,
    )
    assert iterate is False
    assert cycles == 3
    assert budget == 100.0
    assert body.strip() == "do some work"


def test_parse_add_flags_budget_dollar_sign() -> None:
    """``--budget=$50`` must strip the $ and parse."""
    iterate, cycles, budget, body = manager_repl._parse_add_flags(
        "--budget=$50 fix the bug",
    )
    assert budget == 50.0
    assert body.strip() == "fix the bug"


def test_seed_chat_state_downgrades_inherited_continuous_for_memory_backend(
    tmp_path: Path,
) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="disk objective",
        done_reason="planner declared project done",
    )

    chat_state, error = manager_repl._seed_chat_state(
        argparse.Namespace(
            backend="memory",
            continuous=False,
            objective="",
        ),
        mem,
        theme=None,
    )

    assert error is None
    assert chat_state["config"]["continuous"] is False
    assert chat_state["continuous_objective"] == "disk objective"
    state = chat_state["continuous_state"]
    assert state.enabled is False
    assert state.objective == "disk objective"
    assert state.done_reason == "planner declared project done"
    assert state.done_at


def test_seed_chat_state_rejects_explicit_continuous_for_memory_backend(
    tmp_path: Path,
) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()

    chat_state, error = manager_repl._seed_chat_state(
        argparse.Namespace(
            backend="memory",
            continuous=True,
            objective="hardening objective",
        ),
        mem,
        theme=None,
    )

    assert chat_state == {}
    assert error is not None
    assert "cannot plan" in error


def test_backend_cmd_ignores_historical_continuous_objective(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    write_continuous_config(
        tmp_path,
        enabled=False,
        objective="historical objective",
        done_reason="planner declared project done",
    )

    chat_state, error = manager_repl._seed_chat_state(
        argparse.Namespace(
            backend="codex",
            continuous=False,
            objective="",
        ),
        mem,
        theme=None,
    )

    assert error is None
    manager_repl._backend_cmd(["memory"], chat_state)
    out = capsys.readouterr().out
    assert "backend: memory" in out


# ---------------------------------------------------------------------------
# Free-text input applies config defaults
# ---------------------------------------------------------------------------

def test_free_text_uses_config_defaults(mem: LifeMemory) -> None:
    """Free text input must use session config for iteration params, not
    hardcoded defaults."""
    captured: dict[str, Any] = {}

    def fake_invoke(**kwargs: Any) -> dict[str, Any]:
        head = kwargs["mem"].backlog.next_pending()
        captured["head"] = head
        return {"missions_run": 1, "total_cost_usd": 0.0}

    chat_state = {
        "backend": "memory",
        "config": {"iterate": False, "cycles": 2, "budget": 5.0},
    }
    with patch.object(manager_repl, "_invoke_and_track", side_effect=fake_invoke):
        manager_repl._free_text_cmd(mem, "deploy it", chat_state=chat_state)

    head: BacklogItem = captured["head"]
    assert head.iterate is False
    assert head.iteration_max_cycles == 2
    assert head.iteration_budget_usd == 5.0


def test_free_text_inline_flags_override_config(mem: LifeMemory) -> None:
    """``--cycles=8`` in free text must override session config."""
    captured: dict[str, Any] = {}

    def fake_invoke(**kwargs: Any) -> dict[str, Any]:
        head = kwargs["mem"].backlog.next_pending()
        captured["head"] = head
        return {"missions_run": 1, "total_cost_usd": 0.0}

    chat_state = {
        "backend": "memory",
        "config": {"iterate": True, "cycles": 2, "budget": 5.0},
    }
    with patch.object(manager_repl, "_invoke_and_track", side_effect=fake_invoke):
        manager_repl._free_text_cmd(mem, "--cycles=8 refactor the API", chat_state=chat_state)

    head: BacklogItem = captured["head"]
    assert head.iterate is True  # not overridden
    assert head.iteration_max_cycles == 8  # overridden
    assert head.iteration_budget_usd == 5.0  # from config


# ---------------------------------------------------------------------------
# /config command
# ---------------------------------------------------------------------------

def test_config_cmd_show(capsys: pytest.CaptureFixture[str]) -> None:
    """/config with no args shows current values."""
    chat_state: dict[str, Any] = {
        "config": dict(manager_repl._CONFIG_DEFAULTS),
    }
    manager_repl._config_cmd([], chat_state)
    out = capsys.readouterr().out
    assert "cycles" in out
    assert "budget" in out


def test_config_cmd_set_cycles(capsys: pytest.CaptureFixture[str]) -> None:
    """/config cycles=12 changes the setting."""
    chat_state: dict[str, Any] = {
        "config": dict(manager_repl._CONFIG_DEFAULTS),
    }
    manager_repl._config_cmd(["cycles=12"], chat_state)
    assert chat_state["config"]["cycles"] == 12
    out = capsys.readouterr().out
    assert "12" in out


def test_config_cmd_set_iterate_off(capsys: pytest.CaptureFixture[str]) -> None:
    """/config iterate=false turns iteration off."""
    chat_state: dict[str, Any] = {
        "config": dict(manager_repl._CONFIG_DEFAULTS),
    }
    manager_repl._config_cmd(["iterate=false"], chat_state)
    assert chat_state["config"]["iterate"] is False


def test_config_cmd_rejects_bad_key(capsys: pytest.CaptureFixture[str]) -> None:
    """/config badkey=1 prints an error."""
    chat_state: dict[str, Any] = {
        "config": dict(manager_repl._CONFIG_DEFAULTS),
    }
    manager_repl._config_cmd(["badkey=1"], chat_state)
    out = capsys.readouterr().out
    assert "unknown" in out.lower()


def test_config_cmd_rejects_continuous_without_objective(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_state: dict[str, Any] = {
        "backend": "codex",
        "continuous_objective": "",
        "config": dict(manager_repl._CONFIG_DEFAULTS),
    }
    manager_repl._config_cmd(["continuous=true"], chat_state, life_dir=tmp_path)
    out = capsys.readouterr().out
    assert "non-empty --objective" in out
    assert chat_state["config"]["continuous"] is False
    assert not (tmp_path / "continuous.json").exists()


def test_config_cmd_rejects_continuous_on_memory_backend(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_state: dict[str, Any] = {
        "backend": "memory",
        "continuous_objective": "keep going",
        "config": dict(manager_repl._CONFIG_DEFAULTS),
    }
    manager_repl._config_cmd(["continuous=true"], chat_state, life_dir=tmp_path)
    out = capsys.readouterr().out
    assert "cannot plan" in out
    assert chat_state["config"]["continuous"] is False
    assert not (tmp_path / "continuous.json").exists()
