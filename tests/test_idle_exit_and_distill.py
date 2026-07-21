"""Daemon idle auto-exit and graceful operator clock-out."""
from __future__ import annotations

import time

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


# ---- operator clock-out: graceful stop quiesces continuous (别干了) ---------

def test_operator_stop_quiesces_continuous(tmp_path):
    """A graceful stop of a continuous daemon flips continuous.json to
    enabled=false so the campaign does NOT resurrect on the next launch."""
    from argus_skill.daemon import life_worker
    from argus_skill.daemon.life_worker import (
        read_continuous_config,
        write_continuous_config,
    )

    write_continuous_config(tmp_path, enabled=True, objective="run the campaign")
    assert read_continuous_config(tmp_path) == (True, "run the campaign")

    worker = life_worker.LifeWorker.__new__(life_worker.LifeWorker)
    worker.config = _Cfg(continuous=True)
    adopted = life_worker.read_continuous_state(tmp_path)
    worker._quiesce_continuous_on_operator_stop(
        tmp_path,
        adopted.generation,
    )

    enabled, objective = read_continuous_config(tmp_path)
    assert enabled is False  # clocked out — stays dead
    assert objective == "run the campaign"  # preserved so operator can re-arm


def test_operator_stop_noop_when_not_continuous(tmp_path):
    """A non-continuous daemon must not touch continuous.json on stop."""
    from argus_skill.daemon import life_worker
    from argus_skill.daemon.life_worker import (
        read_continuous_config,
        write_continuous_config,
    )

    write_continuous_config(tmp_path, enabled=True, objective="someone else's campaign")
    worker = life_worker.LifeWorker.__new__(life_worker.LifeWorker)
    worker.config = _Cfg(continuous=False)
    worker._quiesce_continuous_on_operator_stop(tmp_path, None)

    enabled, _ = read_continuous_config(tmp_path)
    assert enabled is True  # untouched


def test_operator_stop_does_not_overwrite_newer_same_value_rearm(tmp_path):
    from argus_skill.daemon import life_worker

    life_worker.write_continuous_config(
        tmp_path,
        enabled=True,
        objective="run the campaign",
    )
    adopted = life_worker.read_continuous_state(tmp_path)
    life_worker.write_continuous_config(
        tmp_path,
        enabled=True,
        objective="run the campaign",
    )
    worker = life_worker.LifeWorker.__new__(life_worker.LifeWorker)

    worker._quiesce_continuous_on_operator_stop(
        tmp_path,
        adopted.generation,
    )

    assert life_worker.read_continuous_state(tmp_path).enabled is True
