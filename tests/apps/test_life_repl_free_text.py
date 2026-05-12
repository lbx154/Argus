"""Tests for the unified life REPL helpers in ``argus_skill.apps._life_repl``."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from argus_skill.apps import _life_repl
from argus_skill.daemon.life_worker import write_continuous_config
from argus_skill.life.memory import BacklogItem, LifeMemory


@pytest.fixture()
def mem(tmp_path: Path) -> LifeMemory:
    return LifeMemory.open(root=tmp_path)


def test_add_only_default_priority(mem: LifeMemory, capsys: pytest.CaptureFixture[str]) -> None:
    item = _life_repl._add_only(mem, "do the dishes")
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

    with patch.object(_life_repl, "_invoke_and_track", side_effect=fake_invoke):
        _life_repl._free_text_cmd(mem, "你好", chat_state={"backend": "memory"})

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

    with patch.object(_life_repl, "_invoke_and_track", side_effect=fake_invoke):
        _life_repl._free_text_cmd(mem, "right now please", chat_state={"backend": "memory"})

    assert captured["head_obj"] == "right now please"


# ---------------------------------------------------------------------------
# Singleton lock
# ---------------------------------------------------------------------------

def test_run_life_chat_loop_refuses_concurrent_invocations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second REPL launched while the first holds the lock must
    print a clear error and exit non-zero, NOT silently corrupt
    backlog.jsonl by racing on rewrites."""
    import argparse

    from argus_skill.core.daemon_lock import acquire_global_daemon_lock

    life_dir = tmp_path / "life"
    life_dir.mkdir()

    # Simulate the lock being held by a "first" REPL process.
    # acquire_global_daemon_lock is per-pid_path, so use the same path
    # the REPL would use: <life_dir>/repl.pid.
    lock = acquire_global_daemon_lock(pid_path=life_dir / "repl.pid")
    try:
        ns = argparse.Namespace(life_dir=str(life_dir), color="never", verbose=None)
        rc = _life_repl.run_life_chat_loop(ns)
    finally:
        lock.release()

    assert rc == 2
    captured = capsys.readouterr()
    err = captured.err + captured.out
    assert "another REPL is already running" in err


def test_run_life_chat_loop_releases_lock_on_exit(tmp_path: Path) -> None:
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

    # Patch the inner loop to be a no-op so we just exercise lock+release.
    with patch.object(_life_repl, "_run_life_chat_loop_locked", return_value=0):
        ns = argparse.Namespace(life_dir=str(life_dir), color="never", verbose=None)
        rc = _life_repl.run_life_chat_loop(ns)
    assert rc == 0

    # The lock must be reacquirable now.
    lock = acquire_global_daemon_lock(pid_path=life_dir / "repl.pid")
    try:
        # And taking it again would fail.
        with pytest.raises(DaemonAlreadyRunning):
            acquire_global_daemon_lock(pid_path=life_dir / "repl.pid")
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# _parse_add_flags with session defaults
# ---------------------------------------------------------------------------

def test_parse_add_flags_uses_session_defaults() -> None:
    """When no inline flags are given, the session config defaults are
    used rather than the hardcoded function defaults."""
    iterate, cycles, budget, body = _life_repl._parse_add_flags(
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
    iterate, cycles, budget, body = _life_repl._parse_add_flags(
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
    iterate, cycles, budget, body = _life_repl._parse_add_flags(
        "--budget=$50 fix the bug",
    )
    assert budget == 50.0
    assert body.strip() == "fix the bug"


def test_seed_chat_state_merges_cli_and_disk_continuous(tmp_path: Path) -> None:
    mem = LifeMemory.open(tmp_path)
    mem.init()
    write_continuous_config(
        tmp_path,
        enabled=True,
        objective="disk objective",
        done_reason="planner declared project done",
    )

    chat_state, error = _life_repl._seed_chat_state(
        argparse.Namespace(
            backend="codex",
            continuous=False,
            objective="",
        ),
        mem,
        theme=None,
    )

    assert error is None
    assert chat_state["config"]["continuous"] is True
    assert chat_state["continuous_objective"] == "disk objective"
    state = chat_state["continuous_state"]
    assert state.enabled is True
    assert state.objective == "disk objective"
    assert state.done_reason == ""
    assert state.done_at == ""


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

    chat_state, error = _life_repl._seed_chat_state(
        argparse.Namespace(
            backend="codex",
            continuous=False,
            objective="",
        ),
        mem,
        theme=None,
    )

    assert error is None
    _life_repl._backend_cmd(["memory"], chat_state)
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
    with patch.object(_life_repl, "_invoke_and_track", side_effect=fake_invoke):
        _life_repl._free_text_cmd(mem, "deploy it", chat_state=chat_state)

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
    with patch.object(_life_repl, "_invoke_and_track", side_effect=fake_invoke):
        _life_repl._free_text_cmd(mem, "--cycles=8 refactor the API", chat_state=chat_state)

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
        "config": dict(_life_repl._CONFIG_DEFAULTS),
    }
    _life_repl._config_cmd([], chat_state)
    out = capsys.readouterr().out
    assert "cycles" in out
    assert "budget" in out


def test_config_cmd_set_cycles(capsys: pytest.CaptureFixture[str]) -> None:
    """/config cycles=12 changes the setting."""
    chat_state: dict[str, Any] = {
        "config": dict(_life_repl._CONFIG_DEFAULTS),
    }
    _life_repl._config_cmd(["cycles=12"], chat_state)
    assert chat_state["config"]["cycles"] == 12
    out = capsys.readouterr().out
    assert "12" in out


def test_config_cmd_set_iterate_off(capsys: pytest.CaptureFixture[str]) -> None:
    """/config iterate=false turns iteration off."""
    chat_state: dict[str, Any] = {
        "config": dict(_life_repl._CONFIG_DEFAULTS),
    }
    _life_repl._config_cmd(["iterate=false"], chat_state)
    assert chat_state["config"]["iterate"] is False


def test_config_cmd_rejects_bad_key(capsys: pytest.CaptureFixture[str]) -> None:
    """/config badkey=1 prints an error."""
    chat_state: dict[str, Any] = {
        "config": dict(_life_repl._CONFIG_DEFAULTS),
    }
    _life_repl._config_cmd(["badkey=1"], chat_state)
    out = capsys.readouterr().out
    assert "unknown" in out.lower()


def test_config_cmd_rejects_continuous_without_objective(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat_state: dict[str, Any] = {
        "backend": "codex",
        "continuous_objective": "",
        "config": dict(_life_repl._CONFIG_DEFAULTS),
    }
    _life_repl._config_cmd(["continuous=true"], chat_state, life_dir=tmp_path)
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
        "config": dict(_life_repl._CONFIG_DEFAULTS),
    }
    _life_repl._config_cmd(["continuous=true"], chat_state, life_dir=tmp_path)
    out = capsys.readouterr().out
    assert "cannot plan" in out
    assert chat_state["config"]["continuous"] is False
    assert not (tmp_path / "continuous.json").exists()
