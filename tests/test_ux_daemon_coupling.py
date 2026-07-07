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


def test_fresh_idle_session_does_not_autospawn_on_boot():
    """A fresh IDLE session (no continuous / objective / pending backlog) must
    NOT spawn a daemon on boot — it spawns lazily on the first real task, so an
    empty session never leaves an idle daemon behind. Continuous / objective /
    backlog-bearing sessions still boot their daemon eagerly."""
    fresh = argparse.Namespace(no_daemon=False, continuous=False,
                               resume_continuous=False, objective="")
    assert manager_repl._should_autospawn_on_boot(fresh) is False

    continuous = argparse.Namespace(no_daemon=False, continuous=True,
                                    resume_continuous=False, objective="")
    assert manager_repl._should_autospawn_on_boot(continuous) is True

    with_obj = argparse.Namespace(no_daemon=False, continuous=False,
                                  resume_continuous=False, objective="improve X")
    assert manager_repl._should_autospawn_on_boot(with_obj) is True

    disabled = argparse.Namespace(no_daemon=True, continuous=True,
                                  resume_continuous=False, objective="")
    assert manager_repl._should_autospawn_on_boot(disabled) is False

    # A resumed session with a pending backlog still boots its daemon to drain it.
    class _Backlog:
        def pending(self):
            return [object()]

    class _Mem:
        backlog = _Backlog()

    assert manager_repl._should_autospawn_on_boot(fresh, _Mem()) is True


# ---- /resume switch: _reexec_into_session preserves CLI-only flags -------

def test_reexec_into_session_preserves_life_dir_and_no_daemon(tmp_path, monkeypatch):
    """--life-dir / --no-daemon are CLI-only (no env-var backing), so the
    re-exec argv must carry them through explicitly."""
    captured: dict[str, object] = {}

    def fake_execv(path, argv):
        captured["path"] = path
        captured["argv"] = argv

    monkeypatch.setattr(manager_repl.os, "execv", fake_execv)
    args = argparse.Namespace(life_dir=str(tmp_path / "root"), no_daemon=True)

    manager_repl._reexec_into_session("s-target", args)

    argv = captured["argv"]
    assert argv[:4] == [manager_repl.sys.executable, "-m", "argus_skill", "--resume"]
    assert "s-target" in argv
    assert "--life-dir" in argv
    assert str(tmp_path / "root") in argv
    assert "--no-daemon" in argv


def test_reexec_into_session_resumes_target_continuous_campaign(tmp_path, monkeypatch):
    """Regression: switching into a session whose OWN continuous.json is
    armed must pass --resume-continuous, even though the CURRENT launch's
    ``args`` knows nothing about the TARGET session's campaign state —
    without this, _should_autospawn_on_boot only eagerly starts a daemon for
    a non-empty backlog, so a continuous campaign that fully drained its
    backlog between rounds (and whose daemon died) would silently stay
    un-resumed after an explicit operator /resume switch back into it."""
    from argus_skill.daemon.life_worker import write_continuous_config

    global_root = tmp_path / "root"
    target_dir = global_root / "projects" / "s-target"
    target_dir.mkdir(parents=True)
    write_continuous_config(target_dir, enabled=True, objective="keep optimizing X")

    captured: dict[str, object] = {}

    def fake_execv(path, argv):
        captured["argv"] = argv

    monkeypatch.setattr(manager_repl.os, "execv", fake_execv)
    args = argparse.Namespace(life_dir=None, no_daemon=False)

    manager_repl._reexec_into_session("s-target", args, global_root=global_root)

    assert "--resume-continuous" in captured["argv"]


def test_reexec_into_session_no_flag_when_target_not_continuous(tmp_path, monkeypatch):
    """The common case: switching into an ordinary (non-continuous) session
    must NOT add --resume-continuous — it stays off by default, per that
    flag's own safety rationale, unless the target genuinely has one armed."""
    global_root = tmp_path / "root"
    (global_root / "projects" / "s-target").mkdir(parents=True)

    captured: dict[str, object] = {}

    def fake_execv(path, argv):
        captured["argv"] = argv

    monkeypatch.setattr(manager_repl.os, "execv", fake_execv)
    args = argparse.Namespace(life_dir=None, no_daemon=False)

    manager_repl._reexec_into_session("s-target", args, global_root=global_root)

    assert "--resume-continuous" not in captured["argv"]


def test_reexec_into_session_missing_global_root_is_safe(monkeypatch):
    """No global_root (e.g. an older caller) must not crash the switch — just
    skip the continuous-campaign lookup."""
    captured: dict[str, object] = {}

    def fake_execv(path, argv):
        captured["argv"] = argv

    monkeypatch.setattr(manager_repl.os, "execv", fake_execv)
    args = argparse.Namespace(life_dir=None, no_daemon=False)

    manager_repl._reexec_into_session("s-target", args)  # no global_root kwarg

    assert "--resume-continuous" not in captured["argv"]
    assert "s-target" in captured["argv"]


# ---- daemon boot warnings actually reach the operator --------------------

def test_print_daemon_boot_status_prints_all_three_messages(capsys):
    """Regression: legacy_zombie_msg / auto_spawn_msg / no_daemon_warning were
    being computed by _run_manager_repl_locked's daemon-boot block and then
    silently discarded — confirmed via git blame, a "wip(argus): daemon
    autonomous changes" commit (06aa6914) dropped their print step while
    leaving the message-building logic (and its "warn loudly" / "surface
    this" comments) in place. All three must actually reach stdout."""
    manager_repl._print_daemon_boot_status(
        None,
        legacy_zombie_msg="legacy daemon detected (pid 123, pre-pivot). Run: kill 123",
        auto_spawn_msg="daemon auto-spawned (pid 456)",
        no_daemon_warning="daemon spawn did not confirm alive — backlog may not execute.",
    )
    out = capsys.readouterr().out
    assert "legacy daemon detected (pid 123" in out
    assert "daemon auto-spawned (pid 456)" in out
    assert "daemon spawn did not confirm alive" in out


def test_print_daemon_boot_status_no_output_when_all_none(capsys):
    """The common case (clean boot, no legacy zombie, no warning) prints
    nothing extra."""
    manager_repl._print_daemon_boot_status(
        None, legacy_zombie_msg=None, auto_spawn_msg=None, no_daemon_warning=None,
    )
    assert capsys.readouterr().out == ""


def test_print_daemon_boot_status_colors_with_theme():
    """When a theme is supplied, legacy-zombie/no-daemon warnings are painted
    yellow and a clean auto-spawn note is dim — not plain, uncolored text."""
    calls: list[tuple[str, str]] = []

    class _Theme:
        def yellow(self, s: str) -> str:
            calls.append(("yellow", s))
            return f"<yellow>{s}</yellow>"

        def dim(self, s: str) -> str:
            calls.append(("dim", s))
            return f"<dim>{s}</dim>"

    manager_repl._print_daemon_boot_status(
        _Theme(),
        legacy_zombie_msg="zombie!",
        auto_spawn_msg="spawned ok",
        no_daemon_warning="no daemon!",
    )
    assert ("yellow", "zombie!") in calls
    assert ("dim", "spawned ok") in calls
    assert ("yellow", "no daemon!") in calls


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

def test_tail_wait_spinner_animates_and_reflects_real_activity():
    """The passive event tail is the DEFAULT follow path; without an indicator
    its idle gaps are a frozen blinking cursor ("只有光标闪烁没有内容"). The
    spinner must paint a braille glyph and a role-appropriate -ing verb driven
    by the REAL current role (set_activity), rotating that role's own
    vocabulary — and it must NOT echo the raw log line."""
    import io
    from argus_skill.manager.repl import (
        _TailWaitSpinner, _TAIL_ROLE_VERBS, _TAIL_WAIT_VERBS,
    )
    from argus_skill.cli.live_status import FRAMES

    buf = io.StringIO()
    spin = _TailWaitSpinner(theme=None, stream=buf, enabled=True)

    # Before any event: rotates a "waiting for the daemon" phrase.
    spin.tick()
    assert any(v[:6] in buf.getvalue().lower() for v in _TAIL_WAIT_VERBS)

    # Once a real engineer action is known, the label shows the Engineer role +
    # one of ITS verbs — and never the passed-in log note.
    buf.truncate(0); buf.seek(0)
    spin.set_activity("engineer", "running the baseline check")
    spin.tick()
    out = buf.getvalue()
    assert any(f in out for f in FRAMES), "no braille frame painted"
    assert "Engineer" in out
    assert any(v in out for v in _TAIL_ROLE_VERBS["engineer"]), "no engineer verb"
    assert "running the baseline check" not in out, "must not echo the log note"
    assert "\x1b[2K" in out, "spinner did not use an in-place erase"

    # A different role rotates its OWN vocabulary.
    buf.truncate(0); buf.seek(0)
    spin.set_activity("reviewer")
    spin.tick()
    rout = buf.getvalue()
    assert "Reviewer" in rout
    assert any(v in rout for v in _TAIL_ROLE_VERBS["reviewer"]), "no reviewer verb"

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

def test_tail_printer_collapses_streamed_message_fragments():
    """Copilot forwards a streamed agent message as several ``replace``+
    ``message_id`` beats (growing prefixes, then a duplicate final copy). The
    tail must show ONE clean line per message — not the fragmented + duplicated
    '💭' spam the operator reported."""
    import io
    import contextlib
    from argus_skill.manager import repl
    from argus_skill.apps.cli._follow import _format_follow_event

    def ev(text, mid):
        return {
            "type": "engineer.progress", "kind": "agent_message",
            "text": text, "agent_layer": "engineer",
            "replace": True, "message_id": mid,
        }

    events = [
        ev("The report reused the same", "m1"),
        ev("The report reused the same bash fixture approach", "m1"),
        ev("The report reused the same bash fixture approach — insufficient.", "m1"),
        ev("The report reused the same bash fixture approach — insufficient.", "m1"),  # dup final
        {"type": "engineer.progress", "kind": "command_execution",
         "text": "ls", "action_summary": "ls -la", "agent_layer": "engineer"},
        ev("I'll start by checking the baseline.", "m2"),
        ev("I'll start by checking the baseline.", "m2"),  # short: delta==final dup
    ]

    spin = repl._TailWaitSpinner(theme=None, enabled=False)  # non-TTY: no spinner
    printer = repl._TailPrinter(spin)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        for e in events:
            printer.feed(e, _format_follow_event(e, "engineer", theme=None))
        printer.flush()

    lines = [ln for ln in sink.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 3, f"expected 3 coalesced lines, got {lines!r}"
    # The m1 message shows once, only its FINAL (longest) form, before the tool.
    assert lines[0].endswith("insufficient.")
    assert "▸ ls -la" in lines[1]
    assert lines[2].endswith("I'll start by checking the baseline.")
    # No fragment / duplicate leaked through.
    assert sink.getvalue().count("checking the baseline") == 1


def test_tail_printer_prints_complete_lines_immediately():
    """A line WITHOUT ``replace`` (codex/claude complete beats, tool/mission
    events) must print immediately and unchanged — the coalescer only holds
    streamed ``replace`` messages."""
    import io
    import contextlib
    from argus_skill.manager import repl

    spin = repl._TailWaitSpinner(theme=None, enabled=False)
    printer = repl._TailPrinter(spin)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        printer.feed({"type": "engineer.progress"}, "  [Engineer] 💭 hello")
        printer.feed({"type": "engineer.progress"}, "  [Engineer] 💭 world")
    # Both printed right away (nothing held back), in order.
    assert sink.getvalue() == "  [Engineer] 💭 hello\n  [Engineer] 💭 world\n"


def _fmt_reviewer(event):
    from argus_skill.apps.cli._follow import _format_follow_event
    return _format_follow_event(event, "reviewer", theme=None)


def test_tail_printer_flush_idle_never_commits_mid_stream():
    """The 200-fragment reviewer dump came from ``flush_idle`` committing a
    still-streaming message on every idle poll. ``flush_idle`` must ONLY settle
    a message that has gone quiet for ``_idle_commit_after`` — an
    actively-arriving stream (fresh ``_pending_at``) is never split."""
    import io
    import contextlib
    from argus_skill.manager import repl

    def ev(text, mid):
        return {
            "type": "engineer.progress", "kind": "agent_message",
            "text": text, "agent_layer": "reviewer",
            "replace": True, "message_id": mid,
        }

    spin = repl._TailWaitSpinner(theme=None, enabled=False)
    printer = repl._TailPrinter(spin)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        printer.feed(ev("{", "m1"), "  [Reviewer] 💭 {")
        printer.flush_idle()  # just arrived -> must NOT commit
        printer.feed(ev('{"status":"done"', "m1"), "  [Reviewer] 💭 verdict")
        printer.flush_idle()  # still fresh -> must NOT commit
        assert sink.getvalue() == "", "flush_idle leaked a mid-stream fragment"
        printer._pending_at -= 10.0  # simulate the stream going quiet
        printer.flush_idle()         # now settle exactly ONE line
    lines = [ln for ln in sink.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one settled line, got {lines!r}"


def test_tail_printer_coalesces_raw_delta_fragments():
    """Real copilot/codex beats are RAW non-overlapping chunks plus a final full
    copy (not growing prefixes). Keeping the LONGEST text per message_id must
    still converge on the complete final message — one clean line, JSON parsed
    into a verdict, no raw JSON guts."""
    import io
    import contextlib
    from argus_skill.manager import repl

    def ev(text, mid):
        return {
            "type": "engineer.progress", "kind": "agent_message",
            "text": text, "agent_layer": "reviewer",
            "replace": True, "message_id": mid,
        }

    beats = [
        ev("{", "m1"),
        ev('"status":"done","reason":"re-ran', "m1"),
        ev(" the check myself", "m1"),
        ev('{"status":"done","reason":"re-ran the check myself and confirmed"}', "m1"),
    ]
    spin = repl._TailWaitSpinner(theme=None, enabled=False)
    printer = repl._TailPrinter(spin)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        for b in beats:
            printer.feed(b, _fmt_reviewer(b))
        printer.flush()
    lines = [ln for ln in sink.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one coalesced line, got {lines!r}"
    assert "reviewer verdict: done" in lines[0]
    assert "{" not in lines[0], "raw JSON leaked into the committed line"


def test_clip_follow_summary_cuts_on_word_boundary_with_count():
    """Long agent_message text must be clipped cleanly (word boundary + a
    ``(+N chars)`` hint), not sliced mid-word with a bare ``…``."""
    from argus_skill.apps.cli._follow import _clip_follow_summary

    text = "alpha beta gamma delta " * 40  # ~920 chars
    out = _clip_follow_summary(text, 240)
    assert out.endswith("chars)"), out
    assert "…" in out
    head = out.split(" … (+")[0]
    assert head.split()[-1] in {"alpha", "beta", "gamma", "delta"}, \
        f"cut mid-word: {head[-12:]!r}"
    assert _clip_follow_summary("short and sweet", 240) == "short and sweet"


def test_live_roles_panel_feeds_coalescer_and_returns_completion(tmp_path):
    """The role panel now also drains events into the Ctrl+O reasoning-pane
    coalescer. In a non-TTY context (no keyboard, pane hidden) it must still
    render the panel and return the mission's completion — i.e. the new
    coalescer.feed path never breaks the existing dashboard/return contract."""
    import io
    import json
    import contextlib
    from argus_skill.manager.repl import follow_mission_live_roles

    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({
            "type": "engineer.progress", "kind": "agent_message",
            "text": '{"status":"done","reason":"ok"}', "agent_layer": "reviewer",
            "replace": True, "message_id": "r1", "item_id": "it1",
        }) + "\n"
        + json.dumps({"type": "life.mission.completed", "item_id": "it1",
                      "status": "done"}) + "\n",
        encoding="utf-8",
    )
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        result = follow_mission_live_roles(tmp_path, "it1", theme=None, timeout=5.0)
    assert result is not None and result.get("type") == "life.mission.completed"
    assert "roles" in sink.getvalue()  # the dashboard rendered


def test_wrap_plain_is_width_aware_and_lossless():
    """The reasoning pane must NOT truncate: _wrap_plain word-wraps to the
    display width (CJK = 2 cols), hard-breaks an over-long token, and loses no
    characters — so a long thought is fully readable across rows."""
    import unicodedata
    from argus_skill.manager.repl import _wrap_plain

    def dispw(s):
        return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
                   for c in s)

    en = "alpha beta gamma delta epsilon zeta eta theta iota kappa " * 3
    rows = _wrap_plain(en, 40)
    assert all(dispw(r) <= 40 for r in rows), [dispw(r) for r in rows]
    # word-boundary: no row ends by splitting a word it could have kept whole
    assert len(rows) > 1

    cjk = "判断任务归属然后决定交给哪个角色处理这个二十四乘七的长期任务并持续运行下去"
    crows = _wrap_plain(cjk, 20)
    assert all(dispw(r) <= 20 for r in crows), [dispw(r) for r in crows]
    # lossless: every CJK char survives (ignoring the continuation indent spaces)
    assert "".join(r.strip() for r in crows) == cjk

    # Short text passes through untouched.
    assert _wrap_plain("hi there", 40) == ["hi there"]
