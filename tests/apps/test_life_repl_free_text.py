"""Tests for the Manager REPL helpers in ``argus_skill.manager.repl``.

Conversation-surface helpers live in ``argus_skill.manager.repl``; the
runtime infrastructure they drive (runner factory, supervisor driver,
``_SkillLoopRunner``) lives in ``argus_skill.apps._runtime``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

import argus_skill.adapters.agent_cli_backend as agent_cli_backend_mod
from argus_skill.apps import _runtime
from argus_skill.daemon.life_worker import write_continuous_config
from argus_skill.life import MemoryBundle
from argus_skill.life.memory import Backlog, BacklogItem, LifeMemory
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
    "ARGUS_SKILL_COCKPIT_LIVE",
    "ARGUS_SKILL_ENGINEER_BACKEND",
    "ARGUS_SKILL_ENGINEER_MODEL",
    "ARGUS_SKILL_ENGINEER_REASONING_EFFORT",
    "ARGUS_SKILL_FOLLOW_LIVE",
    "ARGUS_SKILL_HOME",
    "ARGUS_SKILL_LIFE_BACKEND",
    "ARGUS_SKILL_MANAGER_BACKEND",
    "ARGUS_SKILL_MANAGER_REASONING_EFFORT",
    "ARGUS_SKILL_MAX_ROUNDS",
    "ARGUS_SKILL_MODEL",
    "ARGUS_SKILL_PER_MISSION_CAP_USD",
    "ARGUS_SKILL_PLANNER_BACKEND",
    "ARGUS_SKILL_PLANNER_REASONING_EFFORT",
    "ARGUS_SKILL_PLAN_MODE",
    "ARGUS_SKILL_PLAN_MODEL",
    "ARGUS_SKILL_RESEARCH_PROFILE",
    "ARGUS_SKILL_RESEARCH_PROFILE_PATH",
    "ARGUS_SKILL_REVIEWER_BACKEND",
    "ARGUS_SKILL_REVIEWER_MODEL",
    "ARGUS_SKILL_REVIEWER_REASONING_EFFORT",
    "ARGUS_SKILL_RUNNER_BACKEND",
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


@pytest.fixture(autouse=True)
def _assume_daemon_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests exercise the free-text → daemon attach/tail path, which only
    runs when a daemon is alive. T2 added a liveness gate (so the REPL stops
    lying "daemon executing" + freezing when no executor exists); default these
    pre-T2 tests to "daemon alive" so they keep testing the attach path. The
    honest no-daemon path has dedicated tests in test_ux_daemon_coupling.py.
    """
    monkeypatch.setattr(
        manager_repl, "_daemon_alive_for", lambda life_dir: (True, 99999)
    )


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
    assert "- Engineer reasoning effort: xhigh" in captured["runtime_context"]
    assert "- Reviewer reasoning effort: xhigh" in captured["runtime_context"]
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


def test_skill_loop_runner_uses_workdir_for_manager_artifacts_not_session_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the Manager's ``project_root`` / ``_artifact_root`` (where
    ``research/PIPELINE_STATE.json`` and every other stage/vertical artifact
    live) MUST be the real mission workdir, never ``manager_session_root``
    (the daemon's internal life_dir, used only for the Manager's OWN
    persistent codex session/lock files). Every OTHER reader/writer of
    pipeline stage (``stage_checklists.current_stage``/``advance_stage``,
    ``engineer/runner.py``'s stage branching, ``resolve_vertical``) reads the
    WORKDIR — pointing the Manager's stage-authority writes at a different
    root silently splits the pipeline state in two (observed in production: a
    mission whose life_dir-scoped PIPELINE_STATE.json legitimately advanced
    to a late stage while its workdir-scoped copy never existed, so every
    stage-gated check kept falling back to the vertical's first stage
    forever)."""
    repo = tmp_path / "repo"
    session_root = tmp_path / "session"
    repo.mkdir()
    session_root.mkdir()

    class FakeAgentCliBackend:
        def __init__(self, *, backend, default_extra_args=None, stop_event=None, **kwargs):
            self.backend = backend

    monkeypatch.setattr(agent_cli_backend_mod, "AgentCliBackend", FakeAgentCliBackend)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)

    runner = _runtime._SkillLoopRunner(
        argparse.Namespace(
            stop_event=None,
            workdir=str(repo),
            manager_session_root=str(session_root),
        ),
        seed_thread_id=None,
    )

    assert runner.manager.project_root == repo
    assert runner._artifact_root == repo
    assert os.environ["ARGUS_SKILL_ARTIFACT_ROOT"] == str(repo)
    # manager_session_root stays independently life_dir-scoped (session/lock
    # files only) — unaffected by this fix.
    assert runner.manager.manager_session_root == session_root
    assert runner._manager_session_root == session_root


def test_stop_reason_consumes_mission_abort_when_daemon_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REAL daemon runner (``enable_mission_abort_signal=True``, set only
    by ``daemon/life_worker.py:_runner_namespace``) must consume a pending
    mission-abort request written by the Manager's REPL-side runner into the
    shared life_dir, terminating the in-flight round via the SAME watchdog
    path already used for daemon-shutdown interrupts."""
    from argus_skill.tools.mission_control import request_mission_abort

    session_root = tmp_path / "session"
    session_root.mkdir()
    captured: dict[str, Any] = {}

    class FakeAgentCliBackend:
        def __init__(self, *, backend, default_interrupt_reason_provider=None, **kwargs):
            self.backend = backend
            captured["interrupt_provider"] = default_interrupt_reason_provider

    monkeypatch.setattr(agent_cli_backend_mod, "AgentCliBackend", FakeAgentCliBackend)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)

    _runtime._SkillLoopRunner(
        argparse.Namespace(
            stop_event=threading.Event(),
            workdir=None,
            manager_session_root=str(session_root),
            enable_mission_abort_signal=True,
        ),
        seed_thread_id=None,
    )

    provider = captured["interrupt_provider"]
    assert provider is not None
    # Nothing requested yet.
    assert provider() is None

    request_mission_abort(session_root, reason="operator asked to stop")
    reason = provider()
    assert reason == "operator abort requested: operator asked to stop"
    # One-shot: consumed, so the very next poll sees nothing pending.
    assert provider() is None


def test_stop_reason_ignores_mission_abort_when_not_daemon_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A REPL-side runner (``enable_mission_abort_signal`` unset — the
    default for ``manager/repl.py:_ensure_manager_runner``) must NEVER
    consume the abort-request file. That file is written by the Manager's
    OWN SELF-turn tool call; if this same runner's watchdog also consumed
    it, the Manager could kill its own in-flight reply before it finished
    answering the operator."""
    from argus_skill.tools.mission_control import request_mission_abort

    session_root = tmp_path / "session"
    session_root.mkdir()
    captured: dict[str, Any] = {}

    class FakeAgentCliBackend:
        def __init__(self, *, backend, default_interrupt_reason_provider=None, **kwargs):
            self.backend = backend
            captured["interrupt_provider"] = default_interrupt_reason_provider

    monkeypatch.setattr(agent_cli_backend_mod, "AgentCliBackend", FakeAgentCliBackend)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)

    _runtime._SkillLoopRunner(
        argparse.Namespace(
            stop_event=threading.Event(),
            workdir=None,
            manager_session_root=str(session_root),
            # enable_mission_abort_signal intentionally omitted.
        ),
        seed_thread_id=None,
    )

    provider = captured["interrupt_provider"]
    assert provider is not None  # stop_event alone still installs a provider

    path = request_mission_abort(session_root, reason="operator asked to stop")
    assert provider() is None
    # Untouched — a disabled runner must not even peek at (let alone delete)
    # a request file it has no business consuming.
    assert path.exists()


def test_live_mission_status_block_empty_when_nothing_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _make_skill_loop_runner(monkeypatch)
    runner._manager_session_root = tmp_path
    assert runner._live_mission_status_block() == ""


def test_live_mission_status_block_empty_when_no_session_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _make_skill_loop_runner(monkeypatch)
    runner._manager_session_root = None
    assert runner._live_mission_status_block() == ""


def test_live_mission_status_block_reports_running_item_and_abort_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _make_skill_loop_runner(monkeypatch)
    runner._manager_session_root = tmp_path

    backlog = Backlog(tmp_path / "backlog.jsonl")
    backlog.add(
        BacklogItem.new(title="Optimize matmul kernel", objective="make it 2x faster")
    )
    claimed = backlog.claim_next()
    assert claimed is not None

    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps({
            "type": "engineer.progress",
            "text": "editing kernel.cu",
            "ts": time.time(),
            "agent_layer": "engineer",
        }) + "\n",
        encoding="utf-8",
    )

    block = runner._live_mission_status_block()
    assert "## Live mission status" in block
    assert "Optimize matmul kernel" in block
    assert claimed.id in block
    assert str(tmp_path) in block
    assert "python -m argus_skill.tools.mission_control abort" in block
    assert f'--life-dir "{tmp_path}"' in block
    assert "engineer" in block.lower()
    # Full autonomy, per the operator's explicit choice: no confirmation gate.
    assert "no operator confirmation" in block or "no confirmation" in block


def test_live_mission_status_block_never_raises_on_corrupt_backlog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _make_skill_loop_runner(monkeypatch)
    runner._manager_session_root = tmp_path
    (tmp_path / "backlog.jsonl").write_text("not json at all{{{", encoding="utf-8")
    # Fail-soft: a SELF reply must never break because status-gathering did.
    assert runner._live_mission_status_block() == ""


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
    """Regression: typing ``hello`` at the prompt must enqueue ``hello`` at the
    HEAD of the backlog (so the daemon claims it next), not bury it behind a
    stale pending item. Post-fusion the REPL no longer executes — it enqueues
    then attaches via ``tail_mission_events`` — so we assert on the queue state
    and that tail is invoked with the just-enqueued item's id.
    """
    older = mem.backlog.add(BacklogItem.new(
        title="old work",
        objective="finish the base64 helper",
        priority=100,
    ))

    captured: dict[str, Any] = {}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any) -> dict[str, Any]:
        head = mem.backlog.next_pending()
        captured["head_id"] = head.id if head else None
        captured["head_obj"] = head.objective if head else None
        captured["tail_item_id"] = item_id
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "你好", chat_state={"backend": "memory"})

    pending = mem.backlog.pending()
    assert pending, "free-text input must enqueue an item"
    assert pending[0].objective == "你好", (
        "free-text item must be at head of pending queue, "
        f"got: {[(it.priority, it.objective) for it in pending]}"
    )
    assert pending[0].priority < older.priority
    # The REPL must attach to the item it just enqueued (the head), not an
    # older pending item.
    assert captured["head_obj"] == "你好"
    assert captured["tail_item_id"] == pending[0].id


def test_blocked_verdict_sets_question_and_reply_continues(mem: LifeMemory) -> None:
    """A blocked mission with an operator_question must (1) be remembered in
    chat_state and (2) make the next free-text reply CONTINUE the same objective
    (answer appended + queued to inbox), not be triaged as a brand-new task."""
    chat_state: dict[str, Any] = {"backend": "memory"}
    blocked_review = {"status": "blocked", "operator_question": "刷哪两道题？"}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "blocked", "cost_usd": 0.0, "_last_review": blocked_review}

    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "研究 SOL-ExecBench，刷到 SOTA", chat_state=chat_state)

    assert chat_state["blocked_question"] == "刷哪两道题？"
    assert chat_state.get("blocked_item_id")

    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "012 和 005", chat_state=chat_state)

    cont = mem.backlog.pending()[0].objective
    assert "研究 SOL-ExecBench，刷到 SOTA" in cont
    assert "操作员答复：012 和 005" in cont
    inbox_file = manager_repl._life_dir_for(mem) / "inbox.jsonl"
    assert inbox_file.exists() and "012 和 005" in inbox_file.read_text(encoding="utf-8")


def test_non_blocked_outcome_clears_blocked_state(mem: LifeMemory) -> None:
    chat_state: dict[str, Any] = {"blocked_item_id": "x", "blocked_question": "q"}
    manager_repl._record_mission_outcome(
        chat_state, {"item_id": "x", "status": "success", "cost_usd": 0.0})
    assert "blocked_item_id" not in chat_state
    assert "blocked_question" not in chat_state


def test_free_text_beats_aggressive_priority_zero_pending(mem: LifeMemory) -> None:
    """Even if a queued ``/add`` item has priority 0, free text still wins the
    head slot the daemon will claim first."""
    mem.backlog.add(BacklogItem.new(title="crit", objective="critical", priority=0))

    captured: dict[str, Any] = {}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any) -> dict[str, Any]:
        head = mem.backlog.next_pending()
        captured["head_obj"] = head.objective if head else None
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
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
        "Argus",
        "one cockpit, one mode",
        "Type what you need in natural language",
        "Manager",
        "Planner",
        "Engineer",
        "Reviewer",
        "Exit with /exit",
    ):
        assert fragment in out
    assert "/config [key=val ...]" not in out
    assert "/add <text>" not in out


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
    hardcoded defaults — verified on the enqueued backlog item."""
    captured: dict[str, Any] = {}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any) -> dict[str, Any]:
        captured["head"] = mem.backlog.next_pending()
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    chat_state = {
        "backend": "memory",
        "config": {"iterate": False, "cycles": 2, "budget": 5.0},
    }
    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "deploy it", chat_state=chat_state)

    head: BacklogItem = captured["head"]
    assert head.iterate is False
    assert head.iteration_max_cycles == 2
    assert head.iteration_budget_usd == 5.0


def test_free_text_inline_flags_override_config(mem: LifeMemory) -> None:
    """``--cycles=8`` in free text must override session config."""
    captured: dict[str, Any] = {}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any) -> dict[str, Any]:
        captured["head"] = mem.backlog.next_pending()
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    chat_state = {
        "backend": "memory",
        "config": {"iterate": True, "cycles": 2, "budget": 5.0},
    }
    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "--cycles=8 refactor the API", chat_state=chat_state)

    head: BacklogItem = captured["head"]
    assert head.iterate is True  # not overridden
    assert head.iteration_max_cycles == 8  # overridden
    assert head.iteration_budget_usd == 5.0  # from config


# ---------------------------------------------------------------------------
# REPL = attach client: tail_mission_events + _free_text_cmd attach to daemon
# ---------------------------------------------------------------------------

def _write_events(life_dir: Path, events: list[dict[str, Any]]) -> None:
    import json as _json

    path = life_dir / "events.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(_json.dumps(ev) + "\n")


def test_tail_mission_events_renders_unlabelled_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Live progress: engineer.progress carries no item_id, so it must still
    render (it's what shows 'what it's doing'); a sibling item's event is skipped;
    the matching mission.completed still returns."""
    _write_events(
        tmp_path,
        [
            {"type": "engineer.progress", "text": "reading the kernel def"},
            {"type": "engineer.progress", "item_id": "other", "text": "NOT MINE"},
            {"type": "life.mission.completed", "item_id": "me", "status": "blocked",
             "success": False, "cost_usd": 0.1},
        ],
    )
    final = manager_repl.tail_mission_events(tmp_path, "me", timeout=2.0)
    assert final is not None and final["status"] == "blocked"
    out = capsys.readouterr().out
    assert "reading the kernel def" in out
    assert "NOT MINE" not in out


def test_tail_mission_events_returns_completed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pre-written started+completed pair for the item must be returned."""
    _write_events(
        tmp_path,
        [
            {"type": "life.mission.started", "item_id": "it-1", "title": "t"},
            {
                "type": "life.mission.completed",
                "item_id": "it-1",
                "status": "success",
                "success": True,
                "cost_usd": 0.42,
            },
        ],
    )
    final = manager_repl.tail_mission_events(tmp_path, "it-1", timeout=2.0)
    assert final is not None
    assert final["status"] == "success"
    assert final["cost_usd"] == 0.42
    # The shared follow formatter is used to render — confirm it printed.
    out = capsys.readouterr().out
    assert "started" in out
    assert "mission complete" in out


def test_tail_mission_events_ignores_other_items(tmp_path: Path) -> None:
    """Events for a different item_id must not be returned for ours."""
    _write_events(
        tmp_path,
        [
            {
                "type": "life.mission.completed",
                "item_id": "other",
                "status": "success",
            },
        ],
    )
    final = manager_repl.tail_mission_events(tmp_path, "mine", timeout=0.3)
    assert final is None


def test_tail_mission_events_timeout_returns_none_without_completed(
    tmp_path: Path,
) -> None:
    """Started but never completed within a short timeout → None, no raise."""
    _write_events(
        tmp_path,
        [{"type": "life.mission.started", "item_id": "it-2", "title": "t"}],
    )
    final = manager_repl.tail_mission_events(tmp_path, "it-2", timeout=0.3)
    assert final is None


def test_tail_mission_events_missing_file_returns_none(tmp_path: Path) -> None:
    """No events.jsonl yet → tolerate FileNotFound and return None on timeout."""
    final = manager_repl.tail_mission_events(
        tmp_path / "nope", "whatever", timeout=0.3
    )
    assert final is None


def test_tail_mission_events_tolerates_malformed_lines(
    tmp_path: Path,
) -> None:
    """A partial / non-JSON line in the middle must not crash the tail."""
    path = tmp_path / "events.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
        fh.write('{"type":"life.mission.completed","item_id":"it-3","status":"success"}\n')
    final = manager_repl.tail_mission_events(tmp_path, "it-3", timeout=2.0)
    assert final is not None
    assert final["status"] == "success"


def test_free_text_enqueues_and_attaches_to_completed_event(
    mem: LifeMemory, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end (no daemon): _free_text_cmd enqueues a backlog item, then
    tail_mission_events finds a matching life.mission.completed we pre-write
    keyed to the *just-enqueued* item id. Proves the REPL does NOT execute the
    supervisor and instead attaches to the daemon's event stream."""
    import json as _json

    real_tail = manager_repl.tail_mission_events

    def tail_with_seeded_event(life_dir: Any, item_id: str, **kwargs: Any):
        # Simulate the daemon writing the completion for the claimed item.
        path = Path(life_dir) / "events.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps({
                "type": "life.mission.started",
                "item_id": item_id,
                "title": "free text",
            }) + "\n")
            fh.write(_json.dumps({
                "type": "life.mission.completed",
                "item_id": item_id,
                "status": "success",
                "success": True,
                "cost_usd": 1.25,
            }) + "\n")
        return real_tail(life_dir, item_id, **kwargs)

    chat_state: dict[str, Any] = {"backend": "memory"}
    with patch.object(
        manager_repl, "tail_mission_events", side_effect=tail_with_seeded_event
    ):
        manager_repl._free_text_cmd(mem, "build the widget", chat_state=chat_state)

    pending_or_running = mem.backlog.all()
    assert pending_or_running, "free text must enqueue a backlog item"
    item = pending_or_running[0]
    assert item.objective == "build the widget"

    # mission_count incremented from the tailed completed event (not from any
    # in-process supervisor return).
    assert chat_state.get("mission_count") == 1
    assert chat_state.get("last_cost_usd") == 1.25

    out = capsys.readouterr().out
    assert "queued" in out
    assert "done" in out


def test_free_text_no_completion_reports_still_running(
    mem: LifeMemory, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the daemon hasn't completed within the observe window, the REPL
    must say so rather than claim success."""
    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any):
        return None

    chat_state: dict[str, Any] = {"backend": "memory"}
    with patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "long task", chat_state=chat_state)

    out = capsys.readouterr().out
    assert "still running" in out
    assert "/status" in out
    # No completed event was tailed → stats untouched.
    assert "mission_count" not in chat_state or chat_state["mission_count"] == 0


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


def test_config_cmd_sets_role_effort(capsys: pytest.CaptureFixture[str]) -> None:
    chat_state: dict[str, Any] = {
        "config": dict(manager_repl._CONFIG_DEFAULTS),
        "manager_runner": object(),
    }

    manager_repl._config_cmd(["engineer_effort=xhigh", "planner_effort=high"], chat_state)

    assert chat_state["config"]["engineer_effort"] == "xhigh"
    assert chat_state["config"]["planner_effort"] == "high"
    assert os.environ["ARGUS_SKILL_ENGINEER_REASONING_EFFORT"] == "xhigh"
    assert os.environ["ARGUS_SKILL_PLANNER_REASONING_EFFORT"] == "high"
    assert "manager_runner" not in chat_state
    out = capsys.readouterr().out
    assert "engineer_effort = xhigh" in out


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


def test_free_text_role_effort_config_does_not_enqueue(mem: LifeMemory) -> None:
    chat_state: dict[str, Any] = {"backend": "codex", "manager_runner": object()}

    with patch.object(manager_repl, "_ensure_manager_runner") as ensure:
        manager_repl._free_text_cmd(
            mem,
            "把argus里面默认的四角色的推理effort都改成xhigh",
            chat_state=chat_state,
        )

    ensure.assert_not_called()
    assert mem.backlog.pending() == []
    assert "manager_runner" not in chat_state
    assert os.environ["ARGUS_SKILL_MANAGER_REASONING_EFFORT"] == "xhigh"
    assert os.environ["ARGUS_SKILL_PLANNER_REASONING_EFFORT"] == "xhigh"
    assert os.environ["ARGUS_SKILL_ENGINEER_REASONING_EFFORT"] == "xhigh"
    assert os.environ["ARGUS_SKILL_REVIEWER_REASONING_EFFORT"] == "xhigh"
    assert chat_state["config"]["manager_effort"] == "xhigh"
    assert chat_state["config"]["planner_effort"] == "xhigh"
    assert chat_state["config"]["engineer_effort"] == "xhigh"
    assert chat_state["config"]["reviewer_effort"] == "xhigh"


def test_free_text_backend_switch_config_does_not_enqueue(mem: LifeMemory) -> None:
    chat_state: dict[str, Any] = {"backend": "codex", "manager_runner": object()}

    with patch.object(manager_repl, "_ensure_manager_runner") as ensure:
        manager_repl._free_text_cmd(
            mem,
            "把目前的argus默认后端都改成copilot",
            chat_state=chat_state,
        )

    ensure.assert_not_called()
    assert mem.backlog.pending() == []
    assert "manager_runner" not in chat_state
    assert os.environ["ARGUS_SKILL_RUNNER_BACKEND"] == "copilot"
    assert chat_state["config"]["runner_backend"] == "copilot"


def test_free_text_backend_switch_role_specific(mem: LifeMemory) -> None:
    chat_state: dict[str, Any] = {"backend": "codex", "manager_runner": object()}

    manager_repl._free_text_cmd(
        mem,
        "把 reviewer 换成 claude",
        chat_state=chat_state,
    )

    assert mem.backlog.pending() == []
    assert os.environ["ARGUS_SKILL_REVIEWER_BACKEND"] == "claude"
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ


@pytest.mark.parametrize(
    "text",
    [
        "codex 和 claude 哪个好用",
        "帮我用 copilot 写一个函数",
        "今天天气不错",
    ],
)
def test_backend_switch_recognizer_does_not_misfire(text: str) -> None:
    chat_state: dict[str, Any] = {"backend": "codex"}
    assert manager_repl._maybe_handle_backend_switch_text(None, text, chat_state) is False
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ


def test_free_text_model_switch_shared_does_not_enqueue(mem: LifeMemory) -> None:
    chat_state: dict[str, Any] = {"backend": "codex", "manager_runner": object()}

    with patch.object(manager_repl, "_ensure_manager_runner") as ensure:
        manager_repl._free_text_cmd(
            mem,
            "把模型换成 claude-sonnet-5",
            chat_state=chat_state,
        )

    ensure.assert_not_called()
    assert mem.backlog.pending() == []
    assert "manager_runner" not in chat_state
    assert os.environ["ARGUS_SKILL_MODEL"] == "claude-sonnet-5"
    assert chat_state["config"]["model"] == "claude-sonnet-5"


def test_free_text_model_switch_role_specific_prefers_longest_alias(mem: LifeMemory) -> None:
    """Regression: "gpt-5.4" must not shadow the longer "gpt-5.4-mini" id."""
    chat_state: dict[str, Any] = {"backend": "codex", "manager_runner": object()}

    manager_repl._free_text_cmd(
        mem,
        "engineer 的模型换成 gpt-5.4-mini",
        chat_state=chat_state,
    )

    assert mem.backlog.pending() == []
    assert os.environ["ARGUS_SKILL_ENGINEER_MODEL"] == "gpt-5.4-mini"
    assert "ARGUS_SKILL_MODEL" not in os.environ


@pytest.mark.parametrize(
    "text",
    [
        "如果是 Copilot 的话，也能允许他调用 Copilot 的所有模型",
        "claude-opus-4.8 是最强的模型",
        "换成 claude 后端",  # backend switch, not a model switch
    ],
)
def test_model_switch_recognizer_does_not_misfire(text: str) -> None:
    chat_state: dict[str, Any] = {"backend": "codex"}
    assert manager_repl._maybe_handle_model_switch_text(None, text, chat_state) is False
    assert "ARGUS_SKILL_MODEL" not in os.environ


def test_unknown_slash_command_does_not_enter_codex(
    mem: LifeMemory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Theme:
        def gray(self, text: str) -> str:
            return text

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("unknown slash command must not be treated as free text")

    monkeypatch.setattr(manager_repl, "_free_text_cmd", boom)

    manager_repl.dispatch_command(
        "/et", "/et", mem, {"backend": "codex"}, mem.root, _Theme()
    )

    out = capsys.readouterr().out
    assert "unknown command: /et" in out


# ---------------------------------------------------------------------------
# Manager front-end triage: chat short-circuits the backlog, tasks fall through
# ---------------------------------------------------------------------------


class _FakeManagerRunner:
    """Stand-in for the Manager front-end runner used by ``_free_text_cmd``.

    ``chat_reply_if_conversational`` returns whatever ``is_chat`` was set to;
    a True result means "this was conversation, replied front-stage" and the
    REPL must NOT enqueue it.
    """

    def __init__(self, *, is_chat: bool) -> None:
        self._is_chat = is_chat
        self.last_thread_id = "tid-after-chat"
        self.calls: list[str] = []

    def chat_reply_if_conversational(
        self, *, objective: str, sink: Any, seed_thread_id: Any = None
    ) -> bool:
        self.calls.append(objective)
        return self._is_chat


def test_free_text_chat_short_circuits_backlog(mem: LifeMemory) -> None:
    """Conversational free text (Manager says chat) must NOT enqueue a backlog
    item and must NOT tail the daemon — the Manager replies front-stage."""
    fake = _FakeManagerRunner(is_chat=True)
    tail_called: dict[str, bool] = {"hit": False}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any):
        tail_called["hit"] = True
        return None

    chat_state: dict[str, Any] = {"backend": "codex"}
    with patch.object(
        manager_repl, "_ensure_manager_runner", return_value=fake
    ), patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "你好", chat_state=chat_state)

    assert mem.backlog.pending() == [], "chat must not enqueue a backlog item"
    assert fake.calls == ["你好"]
    assert tail_called["hit"] is False, "chat must not attach to the daemon"
    # The front-stage reply's thread id is threaded back for session continuity.
    assert chat_state.get("last_thread_id") == "tid-after-chat"


def test_free_text_task_falls_through_when_not_conversational(
    mem: LifeMemory,
) -> None:
    """When the Manager says NOT chat, the input is enqueued and the REPL
    attaches via tail_mission_events (existing task path, unchanged)."""
    fake = _FakeManagerRunner(is_chat=False)
    captured: dict[str, Any] = {}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any):
        head = mem.backlog.next_pending()
        captured["head_obj"] = head.objective if head else None
        captured["tail_item_id"] = item_id
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    chat_state: dict[str, Any] = {"backend": "codex"}
    with patch.object(
        manager_repl, "_ensure_manager_runner", return_value=fake
    ), patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "build the widget", chat_state=chat_state)

    pending = mem.backlog.pending()
    assert pending, "a task must enqueue a backlog item"
    assert pending[0].objective == "build the widget"
    assert captured["head_obj"] == "build the widget"
    assert captured["tail_item_id"] == pending[0].id


def test_free_text_triage_skipped_when_no_runner(mem: LifeMemory) -> None:
    """When _ensure_manager_runner returns None (e.g. memory backend / build
    failure) the input is treated as a task — no classification, straight to
    backlog + tail. This is the path the existing memory-backend tests rely on."""
    captured: dict[str, Any] = {}

    def fake_tail(life_dir: Any, item_id: str, **kwargs: Any):
        captured["tail_item_id"] = item_id
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    chat_state: dict[str, Any] = {"backend": "memory"}
    with patch.object(
        manager_repl, "_ensure_manager_runner", return_value=None
    ), patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "do the work", chat_state=chat_state)

    pending = mem.backlog.pending()
    assert pending and pending[0].objective == "do the work"
    assert captured["tail_item_id"] == pending[0].id


def test_ensure_manager_runner_memory_backend_returns_none(mem: LifeMemory) -> None:
    """The memory backend never gets front-end triage — _ensure returns None
    and caches the sentinel so subsequent lines also skip triage."""
    chat_state: dict[str, Any] = {"backend": "memory"}
    assert manager_repl._ensure_manager_runner(chat_state, mem) is None
    # Cached sentinel → a second call also returns None without rebuilding.
    assert chat_state["manager_runner"] is manager_repl._MANAGER_RUNNER_UNAVAILABLE
    assert manager_repl._ensure_manager_runner(chat_state, mem) is None


def test_ensure_manager_runner_build_failure_caches_unavailable(
    mem: LifeMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build failure must not raise — it caches the unavailable sentinel so the
    REPL falls back to the task path for every line."""
    monkeypatch.setattr(
        _runtime,
        "build_life_runner",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    chat_state: dict[str, Any] = {"backend": "codex"}
    assert manager_repl._ensure_manager_runner(chat_state, mem) is None
    assert chat_state["manager_runner"] is manager_repl._MANAGER_RUNNER_UNAVAILABLE


def test_ensure_manager_runner_builds_and_caches_runner(
    mem: LifeMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a non-memory backend the helper builds a runner via build_life_runner
    and caches it for reuse."""
    sentinel_runner = object()
    captured: dict[str, Any] = {}

    def fake_build(ns: argparse.Namespace, *, seed_thread_id: Any = None) -> Any:
        captured["backend"] = ns.backend
        return sentinel_runner

    monkeypatch.setattr(_runtime, "build_life_runner", fake_build)
    chat_state: dict[str, Any] = {"backend": "codex"}
    out = manager_repl._ensure_manager_runner(chat_state, mem)
    assert out is sentinel_runner
    assert captured["backend"] == "codex"
    # Cached → the second call returns the same object without rebuilding.
    captured.clear()
    assert manager_repl._ensure_manager_runner(chat_state, mem) is sentinel_runner
    assert captured == {}


def test_ensure_manager_runner_session_root_matches_daemon_convention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the front-door Manager's ``manager_session_root`` MUST be
    the per-project session dir (``mem.project_root``) — the SAME root the
    daemon's own ``_runner_namespace`` passes as
    ``manager_session_root=str(cfg.life_dir)``.

    Before this fix, ``ns`` never set ``manager_session_root`` at all, so the
    REPL front-door ``Manager`` fell back to ``Path.cwd()`` (the git
    worktree) while the daemon's mission-execution ``Manager`` used the
    session-scoped project dir. A Manager-authored custom domain (e.g. an
    operator task that matches no built-in vertical) then got written to the
    WRONG root — invisible to the daemon, which logged a spurious
    ``load_vertical(...): unknown/half-built vertical`` warning and silently
    dropped back to the ``research`` vertical.
    """
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    bundle = MemoryBundle.for_cwd(repo)
    # These two roots must differ in a real bundle — global vs per-project —
    # otherwise this test could not distinguish the bug from the fix.
    assert bundle.root != bundle.project_root

    captured: dict[str, Any] = {}

    def fake_build(ns: argparse.Namespace, *, seed_thread_id: Any = None) -> Any:
        captured["manager_session_root"] = ns.manager_session_root
        return object()

    monkeypatch.setattr(_runtime, "build_life_runner", fake_build)
    chat_state: dict[str, Any] = {"backend": "codex"}
    manager_repl._ensure_manager_runner(chat_state, bundle)

    assert captured["manager_session_root"] == str(bundle.project_root)


def test_manager_divide_user_task_fallback_uses_session_root_not_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: when no cached runner/Manager is available (e.g. build
    failure), ``_manager_divide_user_task``'s fallback ``Manager(...)`` must
    STILL use the session-scoped ``mem.project_root`` — not the git worktree
    — so a degraded divide never splits vertical/domain state onto a root the
    daemon can't see."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    bundle = MemoryBundle.for_cwd(repo)
    bundle.project.root.mkdir(parents=True, exist_ok=True)
    assert bundle.project_root != repo  # worktree vs session dir must differ

    captured: dict[str, Any] = {}

    class _FakeManager:
        def __init__(self, *, project_root: Any, runner: Any = None) -> None:
            captured["project_root"] = project_root

        def divide(self, task: str, *, ask_on_new_domain: bool = False) -> Any:
            class _Division:
                vertical = "research"
                kind = "research"
                regular = True
                stages: list[str] = []

                @staticmethod
                def headline() -> str:
                    return ""

            return _Division()

    monkeypatch.setattr(manager_repl, "_ensure_manager_runner", lambda *a, **k: None)
    monkeypatch.setattr("argus_skill.manager.Manager", _FakeManager)

    manager_repl._manager_divide_user_task(bundle, "some task", {"backend": "codex"})

    assert captured["project_root"] == bundle.project_root


# ---------------------------------------------------------------------------
# _SkillLoopRunner.chat_reply_if_conversational / _maybe_chat_outcome
# ---------------------------------------------------------------------------


def _make_skill_loop_runner(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a _SkillLoopRunner without constructing a real codex backend."""
    runner = _runtime._SkillLoopRunner.__new__(_runtime._SkillLoopRunner)
    runner._args = argparse.Namespace(
        workdir=None, engineer_model="m", engineer_reasoning_effort="high"
    )
    runner._backend = object()
    runner.manager_backend = object()
    runner._current_sink = None
    runner._current_failure_ledger = None
    runner._next_seed_thread_id = None
    runner.last_thread_id = None
    runner._allow_chat_fast_path = False
    return runner


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_chat_reply_if_conversational_true_emits_self_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Manager routes to SELF, the runner emits a front-stage reply."""
    runner = _make_skill_loop_runner(monkeypatch)
    sink = _CollectingSink()

    class _FakeManager:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def is_conversational(self, text: str, *, run_exec: Any = None) -> bool:
            return True

        def route(self, text: str, *, run_exec: Any = None) -> str:
            return "simple"

    # The runner now holds the ONE Manager instance; route through it.
    runner.manager = _FakeManager()

    self_called: dict[str, Any] = {}
    phases: list[str] = []

    def fake_simple_quick_reply(*, objective: str, sink: Any, seed_thread_id: Any = None):
        self_called["objective"] = objective
        sink.handle_event({"type": "loop.start", "text": "SELF: one Codex handling 你好"})
        sink.handle_event({"type": "engineer.progress", "text": "reading context"})
        sink.handle_event({"type": "round.main.completed", "last_message": "你好!"})
        return _runtime._Outcome(
            success=True, status="done", stop_reason="", rounds=1,
            last_thread_id=None, chat_mode=False,
        )

    monkeypatch.setattr(runner, "_simple_quick_reply", fake_simple_quick_reply)

    assert runner.chat_reply_if_conversational(
        objective="你好", sink=sink, phase_cb=phases.append
    ) is True
    assert self_called["objective"] == "你好"
    assert any(e.get("type") == "round.main.completed" for e in sink.events)
    assert any("SELF: one Codex handling" in p for p in phases)
    assert any("Manager · reading context" in p for p in phases)


def test_maybe_chat_outcome_false_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Manager classifies as a task, _maybe_chat_outcome returns None
    and chat_reply_if_conversational returns False (no chat reply emitted)."""
    runner = _make_skill_loop_runner(monkeypatch)
    sink = _CollectingSink()

    class _FakeManager:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def is_conversational(self, text: str, *, run_exec: Any = None) -> bool:
            return False

        def route(self, text: str, *, run_exec: Any = None) -> str:
            return "complex"

    # The runner now holds the ONE Manager instance; route through it.
    runner.manager = _FakeManager()

    def boom(**kwargs: Any) -> Any:  # must NOT be called on the task path
        raise AssertionError("_chat_quick_reply called for a task")

    monkeypatch.setattr(runner, "_chat_quick_reply", boom)

    assert runner._maybe_chat_outcome(objective="build the thing", sink=sink) is None
    assert runner.chat_reply_if_conversational(
        objective="build the thing", sink=sink
    ) is False
    assert sink.events == []
