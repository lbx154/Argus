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
from pathlib import Path
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
        "life.manager.started",
        "life.manager.completed",
    ]
    assert events[-1]["agent_layer"] == "manager"
    assert events[-1]["vertical"]


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
