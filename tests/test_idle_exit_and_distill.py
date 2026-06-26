"""Phase 2: daemon idle auto-exit + shutdown skill distillation.

Idle-exit lives in the supervisor (it judges "no work for too long" →
``stopped_by=idle_timeout``); the daemon acts on that verdict by exiting its
outer loop, which triggers a final idempotent ``tidy_after_mission`` pass.
"""
from __future__ import annotations

import time

import pytest

from argus_skill.life.supervisor import _core as sup_core
from argus_skill.life.supervisor._core import _idle_exit_seconds


class _Cfg:
    def __init__(self, continuous: bool):
        self.continuous = continuous


class _FakeSup:
    """Minimal stand-in exposing only what the idle-timeout helpers touch."""

    def __init__(self, continuous=True):
        self.config = _Cfg(continuous)
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        self._idle_since = None
        self._last_open_ended_project_done_signature = ""

    # bind the real methods under test
    _enter_idle_backoff = sup_core.LifeSupervisor._enter_idle_backoff
    _reset_idle_backoff = sup_core.LifeSupervisor._reset_idle_backoff
    _maybe_idle_timeout = sup_core.LifeSupervisor._maybe_idle_timeout
    _idle_backoff_seconds = sup_core.LifeSupervisor._idle_backoff_seconds


# ---- env knob -------------------------------------------------------------

def test_idle_exit_seconds_default_and_override(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", raising=False)
    assert _idle_exit_seconds() == 30.0 * 60.0
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "5")
    assert _idle_exit_seconds() == 5 * 60.0
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "0")  # disabled
    assert _idle_exit_seconds() == 0.0
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "garbage")
    assert _idle_exit_seconds() == 30.0 * 60.0  # bad value -> default


# ---- idle clock semantics -------------------------------------------------

def test_idle_clock_set_on_first_idle_and_cleared_on_work():
    s = _FakeSup()
    assert s._idle_since is None
    s._enter_idle_backoff()
    first = s._idle_since
    assert first is not None
    s._enter_idle_backoff()  # second idle pass must NOT reset the clock
    assert s._idle_since == first
    s._reset_idle_backoff()  # a real mission ran
    assert s._idle_since is None


def test_idle_timeout_only_after_cap(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "10")  # 600s
    s = _FakeSup(continuous=True)
    s._enter_idle_backoff()
    # fresh idle -> within window
    assert s._maybe_idle_timeout() == ""
    # backdate the idle clock past the cap
    s._idle_since = time.monotonic() - 601
    assert s._maybe_idle_timeout() == "idle_timeout"


def test_idle_timeout_disabled_and_non_continuous(monkeypatch):
    # disabled via 0
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "0")
    s = _FakeSup(continuous=True)
    s._enter_idle_backoff()
    s._idle_since = time.monotonic() - 10_000
    assert s._maybe_idle_timeout() == ""
    # non-continuous never idle-exits (backlog_empty already handles that path)
    monkeypatch.setenv("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "1")
    s2 = _FakeSup(continuous=False)
    s2._enter_idle_backoff()
    s2._idle_since = time.monotonic() - 10_000
    assert s2._maybe_idle_timeout() == ""


# ---- daemon shutdown distillation ----------------------------------------

class _Backend:
    def __init__(self, backend):
        self.backend = backend


def test_distill_on_shutdown_calls_tidy(monkeypatch):
    from argus_skill.daemon import life_worker

    calls = {}

    def fake_tidy(project_root, runner, **kw):
        calls["root"] = project_root
        calls["runner"] = runner
        return {"to_builtin": 1, "to_vertical": 0, "stayed": 2, "errors": 0}

    monkeypatch.setattr(
        "argus_skill.manager.skill_tidy.tidy_after_mission", fake_tidy
    )

    class _Sup:
        runner = object()

        def _project_workdir(self):
            return "/work/dir"

    # call the real method on a bare instance (no full daemon construction)
    worker = life_worker.LifeWorker.__new__(life_worker.LifeWorker)
    worker.config = _Backend("codex")  # real backend -> distillation runs
    worker._distill_on_shutdown(_Sup())
    assert calls["root"] == "/work/dir"


def test_distill_on_shutdown_skips_memory_backend(monkeypatch):
    from argus_skill.daemon import life_worker

    called = {"n": 0}

    def fake_tidy(*a, **k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(
        "argus_skill.manager.skill_tidy.tidy_after_mission", fake_tidy
    )

    class _Sup:
        runner = object()

        def _project_workdir(self):
            return "/work/dir"

    worker = life_worker.LifeWorker.__new__(life_worker.LifeWorker)
    worker.config = _Backend("memory")  # no real runner -> must NOT distill
    worker._distill_on_shutdown(_Sup())
    assert called["n"] == 0


def test_distill_on_shutdown_is_failsoft(monkeypatch):
    from argus_skill.daemon import life_worker

    def boom(*a, **k):
        raise RuntimeError("tidy exploded")

    monkeypatch.setattr(
        "argus_skill.manager.skill_tidy.tidy_after_mission", boom
    )

    class _Sup:
        runner = object()

        def _project_workdir(self):
            return "/work/dir"

    worker = life_worker.LifeWorker.__new__(life_worker.LifeWorker)
    worker.config = _Backend("codex")
    # must not raise even though tidy explodes
    worker._distill_on_shutdown(_Sup())
