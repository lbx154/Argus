"""UX-A: daemon↔session coupling + honest no-executor messaging.

The "卡住" bug: a `s-…` session REPL auto-spawned a daemon on the *cwd-legacy*
project while queueing the task into the *session* project — two different
backlogs, so the task never ran. T1 makes the auto-spawn target the session
bundle. T2 stops the REPL from claiming "daemon executing" (and freezing in a
600s event-tail) when no daemon is actually running.
"""
from __future__ import annotations

import argparse
import json
import pytest
from unittest.mock import patch

from argus_skill.life import MemoryBundle
from argus_skill.life.memory import LifeMemory
from argus_skill.manager import repl as manager_repl

# ---- T1: auto-spawn targets the session project, not the cwd -------------

def test_build_worker_config_uses_bundle_override(tmp_path):
    from argus_skill.apps.cli._core import _build_worker_config

    gr = tmp_path / "root"
    session = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-abc12345")
    args = argparse.Namespace(
        life_dir=str(gr), backend="memory", continuous=False, objective="", bounded=False
    )
    cfg = _build_worker_config(args, bundle=session)
    # The daemon's life_dir MUST equal the session's project root, so the task
    # the REPL queued into this session is the one the daemon drains.
    assert cfg.life_dir == session.project.root
    assert cfg.project_fingerprint == "s-abc12345"


def test_build_worker_config_without_bundle_resolves_cwd(tmp_path):
    from argus_skill.apps.cli._core import _build_worker_config

    args = argparse.Namespace(
        life_dir=str(tmp_path), backend="memory", continuous=False,
        objective="", bounded=False,
    )
    cfg = _build_worker_config(args)  # no bundle -> legacy cwd resolve (unchanged)
    assert cfg.life_dir.name  # resolved to *some* project dir, no crash


def test_fresh_idle_session_autospawns_on_boot():
    """Bare `argus` starts an executor, but it is keyed to the fresh session."""
    fresh = argparse.Namespace(no_daemon=False, continuous=False, session_is_new=True)
    assert manager_repl._should_autospawn_on_boot(fresh) is True

    resumed = argparse.Namespace(no_daemon=False, continuous=False, session_is_new=False)
    assert manager_repl._should_autospawn_on_boot(resumed) is True

    continuous = argparse.Namespace(no_daemon=False, continuous=True, session_is_new=True)
    assert manager_repl._should_autospawn_on_boot(continuous) is True

    disabled = argparse.Namespace(no_daemon=True, continuous=True, session_is_new=False)
    assert manager_repl._should_autospawn_on_boot(disabled) is False


def test_first_task_autostarts_daemon_for_fresh_session(tmp_path, monkeypatch):
    """After the operator enters a task, the daemon starts on that session bundle."""
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-fresh001")
    mem.init()
    captured: dict[str, object] = {}

    def fake_build_worker_config(args: argparse.Namespace, *, bundle: object = None):
        captured["bundle"] = bundle
        return argparse.Namespace(life_dir=mem.project.root)

    class _Status:
        alive = True
        pid = 4242

    monkeypatch.setattr(manager_repl, "_daemon_alive_for", lambda life_dir: (False, None))
    monkeypatch.setattr("argus_skill.apps.cli._build_worker_config", fake_build_worker_config)
    monkeypatch.setattr(manager_repl, "_spawn_daemon_from_cockpit", lambda cfg: 0)
    monkeypatch.setattr(
        "argus_skill.daemon.life_worker.wait_for_daemon_status",
        lambda life_dir: _Status(),
    )

    item, alive, pid = manager_repl.enqueue_mission(
        mem,
        "do real work",
        {
            "backend": "memory",
            "config": {"continuous": False},
            "auto_start_daemon_on_task": True,
            "open_ended": True,
        },
    )

    assert item.objective == "do real work"
    assert alive is True and pid == 4242
    assert captured["bundle"] is mem


def test_user_task_is_manager_divided_before_enqueue(tmp_path, monkeypatch):
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-user001")
    mem.init()
    monkeypatch.setattr(manager_repl, "_daemon_alive_for", lambda life_dir: (False, None))

    # The Manager decides the vertical via ONE grounded agent call (no keyword
    # fallback); in real cockpit use there is always an LLM runner. Inject a
    # manager backed by a fake decision runner so the pre-enqueue division runs.
    from argus_skill.manager import Manager

    class _DecisionResult:
        def __init__(self, msg: str) -> None:
            self.last_agent_message = msg
            self.agent_messages = [msg]
            self.thread_id = "t1"

    class _DecisionRunner:
        def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
            return _DecisionResult('{"choice": "existing", "vertical": "research"}')

    class _RunnerWithManager:
        def __init__(self, project_root):
            self.manager = Manager(project_root=project_root, runner=_DecisionRunner())

    monkeypatch.setattr(
        manager_repl,
        "_ensure_manager_runner",
        lambda chat_state, mem_: _RunnerWithManager(mem_.project.root),
    )

    item, _alive, _pid = manager_repl.enqueue_mission(
        mem,
        "write a research report",
        {"backend": "memory", "config": {"continuous": False}},
    )

    events = [
        json.loads(line)
        for line in (mem.project.root / "events.jsonl").read_text().splitlines()
    ]
    assert item.objective == "write a research report"
    assert [event["type"] for event in events] == [
        "life.manager.intent.started",
        "life.manager.intent.completed",
    ]
    assert events[-1]["agent_layer"] == "manager"
    assert events[-1]["vertical"] == "research"
    assert events[-1]["objective"] == "write a research report"
    assert events[-1]["reason"]


def test_continuous_user_task_arms_planner_not_direct_engineer_item(
    tmp_path, monkeypatch
):
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-plan001")
    mem.init()
    monkeypatch.setattr(manager_repl, "_daemon_alive_for", lambda life_dir: (False, None))

    item, _alive, _pid = manager_repl.enqueue_mission(
        mem,
        "build an autonomous research report",
        {
            "backend": "memory",
            "config": {"continuous": True},
        },
    )

    assert item is None
    assert mem.backlog.all() == []
    continuous = json.loads((mem.project.root / "continuous.json").read_text())
    assert continuous["enabled"] is True
    assert continuous["objective"] == "build an autonomous research report"


# ---- T3: Manager auto-judges BOUNDED vs STANDING so the operator never has
# to type --continuous --objective for open-ended chat tasks ---------------

def test_auto_promote_to_continuous_arms_continuous_mode_for_standing_task(
    tmp_path, monkeypatch
):
    """A plain chat task Manager judges open-ended ("optimize as many kernels
    as possible") is auto-armed as a standing campaign — same effect as typing
    ``/continuous start <objective>``, but without the operator ever having to
    know that command (or pass --continuous --objective) exists."""
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-standing001")
    mem.init()

    class _FakeRunner:
        def classify_needs_continuous(self, objective: str) -> bool:
            return True

    monkeypatch.setattr(
        manager_repl, "_ensure_manager_runner", lambda chat_state, mem_: _FakeRunner()
    )

    chat_state: dict[str, object] = {"backend": "codex"}
    body = "optimize as many kernels as possible, keep going until none are left"
    promoted = manager_repl._maybe_auto_promote_to_continuous(mem, body, chat_state, None)

    assert promoted is True
    assert chat_state["config"]["continuous"] is True
    assert chat_state["continuous_objective"] == body
    continuous = json.loads((mem.project.root / "continuous.json").read_text())
    assert continuous["enabled"] is True
    assert continuous["objective"] == body


def test_auto_promote_to_continuous_leaves_bounded_task_unchanged(tmp_path, monkeypatch):
    """A well-scoped task with a natural finish line stays on the normal
    bounded (one-shot backlog) path — continuous.json is never written."""
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-bounded001")
    mem.init()

    class _FakeRunner:
        def classify_needs_continuous(self, objective: str) -> bool:
            return False

    monkeypatch.setattr(
        manager_repl, "_ensure_manager_runner", lambda chat_state, mem_: _FakeRunner()
    )

    chat_state: dict[str, object] = {"backend": "codex"}
    promoted = manager_repl._maybe_auto_promote_to_continuous(
        mem, "fix the flaky test in test_foo.py", chat_state, None
    )

    assert promoted is False
    assert "continuous" not in chat_state.get("config", {})
    assert not (mem.project.root / "continuous.json").exists()


def test_auto_promote_to_continuous_fails_soft_without_runner(tmp_path, monkeypatch):
    """Memory backend / build failure -> no runner -> stays bounded, never
    crashes the free-text path."""
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-norunner001")
    mem.init()
    monkeypatch.setattr(manager_repl, "_ensure_manager_runner", lambda chat_state, mem_: None)

    chat_state: dict[str, object] = {"backend": "memory"}
    promoted = manager_repl._maybe_auto_promote_to_continuous(
        mem, "optimize as many kernels as possible", chat_state, None
    )
    assert promoted is False


def test_auto_promote_to_continuous_fails_soft_on_classify_error(tmp_path, monkeypatch):
    """A classify hiccup must never force an expensive 7x24 campaign."""
    gr = tmp_path / "root"
    mem = MemoryBundle.for_cwd(tmp_path, global_root=gr, fingerprint="s-classifyerr001")
    mem.init()

    class _BoomRunner:
        def classify_needs_continuous(self, objective: str) -> bool:
            raise RuntimeError("boom")

    monkeypatch.setattr(
        manager_repl, "_ensure_manager_runner", lambda chat_state, mem_: _BoomRunner()
    )

    chat_state: dict[str, object] = {"backend": "codex"}
    promoted = manager_repl._maybe_auto_promote_to_continuous(mem, "anything", chat_state, None)
    assert promoted is False
    assert not (mem.project.root / "continuous.json").exists()


# ---- T2: honest messaging + no freeze when no daemon ---------------------

def test_no_executor_notice_is_honest_and_actionable():
    msg = manager_repl._no_executor_notice("it-9", theme=None)
    assert "NO daemon" in msg
    assert "will NOT execute" in msg
    assert "argus-skill --daemon" in msg  # the fix
    assert "/doctor" in msg               # the diagnosis
    assert "it-9" in msg
    # crucially it must NOT claim the daemon is executing
    assert "daemon executing" not in msg


def test_daemon_alive_for_is_failsoft(tmp_path):
    # No daemon.pid in a fresh dir -> (False, None), never raises.
    alive, pid = manager_repl._daemon_alive_for(tmp_path)
    assert alive is False and pid is None


def test_free_text_no_daemon_does_not_tail_or_lie(tmp_path, capsys):
    """The core anti-freeze guarantee: with no daemon, free text prints the
    honest notice and returns immediately — it never enters tail_mission_events
    (the old 600s freeze) and never prints "daemon executing"."""
    mem = LifeMemory.open(root=tmp_path)
    tail_called = {"n": 0}

    def boom_tail(*a, **k):
        tail_called["n"] += 1
        raise AssertionError("tail_mission_events must NOT run when no daemon")

    with patch.object(manager_repl, "_daemon_alive_for", return_value=(False, None)), \
         patch.object(manager_repl, "tail_mission_events", side_effect=boom_tail):
        manager_repl._free_text_cmd(mem, "do the work now", chat_state={"backend": "memory"})

    out = capsys.readouterr().out
    assert tail_called["n"] == 0
    assert "NO daemon" in out
    assert "daemon executing" not in out
    # the task is still queued (saved for when a daemon starts)
    assert any(it.objective == "do the work now" for it in mem.backlog.pending())


def test_free_text_with_daemon_attaches_and_shows_pid(tmp_path, capsys):
    mem = LifeMemory.open(root=tmp_path)

    def fake_tail(life_dir, item_id, **k):
        return {"type": "life.mission.completed", "item_id": item_id,
                "status": "success", "cost_usd": 0.0}

    with patch.object(manager_repl, "_daemon_alive_for", return_value=(True, 4242)), \
         patch.object(manager_repl, "tail_mission_events", side_effect=fake_tail):
        manager_repl._free_text_cmd(mem, "right now", chat_state={"backend": "memory"})

    out = capsys.readouterr().out
    assert "pid 4242" in out  # honest: shows the real executor
    assert "NO daemon" not in out


def test_free_text_cmd_end_to_end_auto_promotes_standing_task(tmp_path, capsys, monkeypatch):
    """Full wiring check: a bare ``argus`` chat task that Manager judges
    open-ended flows through ``_free_text_cmd`` straight into the SAME
    continuous hand-off the operator would get from
    ``--continuous --objective`` — with no daemon restart and no manual flags."""
    mem = LifeMemory.open(root=tmp_path)

    class _FakeRunner:
        def chat_reply_if_conversational(self, **kwargs):
            return False  # TEAM: not a chat reply

        def classify_needs_continuous(self, objective: str) -> bool:
            return True

    def fake_follow(life_dir, **kwargs):
        return {"type": "life.mission.completed", "status": "success", "cost_usd": 0.0}

    monkeypatch.setattr(
        manager_repl, "_ensure_manager_runner", lambda chat_state, mem_: _FakeRunner()
    )
    monkeypatch.setattr(manager_repl, "_daemon_alive_for", lambda life_dir: (True, 4242))
    monkeypatch.setattr(manager_repl, "_follow_events_stream", fake_follow)

    chat_state: dict[str, object] = {"backend": "codex"}
    body = "optimize as many kernels as possible"
    manager_repl._free_text_cmd(mem, body, chat_state)

    out = capsys.readouterr().out
    assert "objective handed to Planner" in out
    assert "continuous" in out
    assert chat_state["config"]["continuous"] is True
    continuous = json.loads((tmp_path / "continuous.json").read_text())
    assert continuous["enabled"] is True
    assert continuous["objective"] == body
    # BOUNDED path untouched: no backlog item was created for this objective.
    assert mem.backlog.all() == []


# ---- follow-panel animation: no dead/frozen window while observing ---------

def test_follow_mission_live_roles_animates_spinner(tmp_path, capsys):
    """The live four-role panel must visibly ANIMATE while it waits for the
    daemon's first event — otherwise the pre-first-event window looks frozen
    ("无动画空窗期 / 卡住"). Assert several DISTINCT braille frames are drawn
    over a short observe window that never sees a completion event."""
    from argus_skill.cli.live_status import FRAMES
    from argus_skill.manager.repl import follow_mission_live_roles

    # No events.jsonl completion → the loop just refreshes until it times out.
    final = follow_mission_live_roles(
        tmp_path, item_id="nope", theme=None, timeout=0.5,
    )
    assert final is None  # timed out without a completion, daemon keeps running

    out = capsys.readouterr().out
    # At least two DIFFERENT spinner frames must have been drawn (proves the
    # header spinner advanced across refreshes instead of a static panel).
    frames_seen = {ch for ch in FRAMES if ch in out}
    assert len(frames_seen) >= 2, f"spinner did not animate; frames seen={frames_seen!r}"

def test_with_manager_spinner_runs_fn_exactly_once():
    """The TEAM-handoff spinner helper wraps a blocking model call
    (``Manager.divide`` / daemon auto-spawn). It MUST run ``fn`` exactly once
    (a naive try/except spinner would re-run ``fn`` on error — a double model
    call) and return its value. theme=None → animation is a no-op, logic same."""
    from argus_skill.manager.repl import _with_manager_spinner

    calls = []

    def _fn():
        calls.append(1)
        return "vertical:research"

    result = _with_manager_spinner(None, "Manager choosing the vertical…", _fn)
    assert result == "vertical:research"
    assert calls == [1]  # exactly once — never re-invoked


def test_with_manager_spinner_propagates_fn_error_without_rerun():
    """An exception raised by ``fn`` must propagate unchanged and ``fn`` must
    NOT be retried (guards the double-execution bug: a blocking model call
    running twice on failure)."""
    from argus_skill.manager.repl import _with_manager_spinner

    calls = []

    def _boom():
        calls.append(1)
        raise RuntimeError("vertical decision failed")

    with pytest.raises(RuntimeError, match="vertical decision failed"):
        _with_manager_spinner(None, "Manager choosing the vertical…", _boom)
    assert calls == [1]  # ran once, not retried by the spinner wrapper

def test_tail_wait_spinner_animates_and_clears():
    """The passive event tail is the DEFAULT follow path; without an indicator
    its idle gaps are a frozen blinking cursor ("只有光标闪烁没有内容"). The
    spinner must paint a braille glyph + an -ing phrase on tick() and erase it
    on clear() so a real event line prints on a clean line."""
    import io
    from argus_skill.manager.repl import _TailWaitSpinner
    from argus_skill.cli.live_status import FRAMES

    buf = io.StringIO()
    spin = _TailWaitSpinner(theme=None, stream=buf, enabled=True)
    spin.tick()
    spin.tick()
    out = buf.getvalue()
    assert any(f in out for f in FRAMES), "no braille frame painted"
    assert "…" in out and any(
        v in out for v in ("Thinking", "Planning", "Waiting", "Working",
                            "Reasoning", "Analyzing", "Reviewing")
    ), "no -ing phrase painted"
    assert "\x1b[2K" in out, "spinner did not use an in-place erase"

    buf.truncate(0); buf.seek(0)
    spin.clear()
    assert "\x1b[2K" in buf.getvalue(), "clear() did not erase the status line"


def test_tail_wait_spinner_is_noop_when_disabled():
    """No-op on non-TTY / piped / NO_COLOR: writes NOTHING, so the scrolling
    tail's captured (piped) output stays byte-for-byte unchanged."""
    import io
    from argus_skill.manager.repl import _TailWaitSpinner

    buf = io.StringIO()
    spin = _TailWaitSpinner(theme=None, stream=buf, enabled=False)
    spin.tick()
    spin.tick()
    spin.clear()
    assert buf.getvalue() == "", "disabled spinner must not write anything"
