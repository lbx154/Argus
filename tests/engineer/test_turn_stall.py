"""A turn that stops progressing is reported to the Manager once and stopped only on its word."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import argus.agent_cli._run_exec as run_exec_module
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.engineer.round_stop_signals import (
    fatal_error_is_idle_termination,
    idle_termination_running_tool,
)
from argus.engineer.turn_stall import TurnStallWatch
from argus.manager.directive import set_active_manager_directive


def _watch(tmp_path: Path, events: list[dict], stall_seconds: float = 1800) -> TurnStallWatch:
    return TurnStallWatch(
        stall_seconds=stall_seconds, state_root=tmp_path, run_label="engineer-r2", on_event=events.append,
    )


def _look(watch: TurnStallWatch, idle: float, tool: str = "sleep 9999") -> str | None:
    return watch(SimpleNamespace(idle_seconds=idle, running_tool=tool))


def test_progress_ends_the_stall_and_the_next_stall_is_reported_again(tmp_path: Path) -> None:
    events: list[dict] = []
    watch = _watch(tmp_path, events)

    _look(watch, 1800)
    _look(watch, 2400)
    _look(watch, 60)  # the command wrote or computed something in between
    _look(watch, 1800)

    assert len(events) == 2


def test_only_a_manager_decision_after_the_report_restarts_the_turn(tmp_path: Path) -> None:
    events: list[dict] = []
    set_active_manager_directive(tmp_path, "an older course", source="manager.supervision:old")
    watch = _watch(tmp_path, events)

    assert _look(watch, 1800) is None
    assert _look(watch, 1860) is None  # the directive that predates the stall decides nothing
    set_active_manager_directive(tmp_path, "operator words", source="manager.steer")
    assert _look(watch, 1920) is None  # only the Manager's supervision answers a stall
    set_active_manager_directive(tmp_path, "Kill the hung download and mirror it.", source="manager.supervision:new")

    assert _look(watch, 1980) == "restart: the Manager decided to restart the stalled turn"


# The real streaming loop, driven by a provider stand-in that starts a command
# and waits on it without printing, as an agent CLI does while a tool runs.
_PROVIDER = """
import json, subprocess, sys
print(json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": sys.argv[1]}}), flush=True)
subprocess.run([sys.executable, "-c", sys.argv[2]])
print(json.dumps({"type": "turn.completed"}), flush=True)
"""


def _run_turn(tool: str, code: str, watch: TurnStallWatch, *, stall_seconds: int, started=None):
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex", event_callback=lambda *_: None)
    command = [sys.executable, "-u", "-c", _PROVIDER, tool, code]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", start_new_session=True,
    )
    if started is not None:
        started.append(process)
    try:
        state = runner._stream_turn_output(
            process=process,
            command=command,
            options=RunnerOptions(
                inactivity_callback=watch,
                watchdog_soft_idle_seconds=1,
                watchdog_stalled_idle_seconds=stall_seconds,
                watchdog_hard_idle_seconds=0,
            ),
            run_label="engineer-r2",
            thread_id=None,
        )
        return state, process
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_a_hung_tool_is_reported_once_and_stopped_only_after_the_manager_decides(tmp_path: Path) -> None:
    events: list[dict] = []
    watch = _watch(tmp_path, events, stall_seconds=2)
    turns: list[subprocess.Popen] = []
    alive_before_the_decision: list[bool] = []

    def manager() -> None:
        deadline = time.monotonic() + 20
        while not events and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(2.5)  # more looks pass with no decision
        alive_before_the_decision.append(turns[0].poll() is None)
        set_active_manager_directive(tmp_path, "Stop the hung fetch; use the local mirror.",
                                     source="manager.supervision:decided")

    decider = threading.Thread(target=manager, daemon=True)
    decider.start()
    started = time.monotonic()
    state, _process = _run_turn(
        "curl https://stuck.example", "import time; time.sleep(60)", watch, stall_seconds=2, started=turns,
    )
    decider.join(timeout=5)

    assert time.monotonic() - started < 30
    assert len(events) == 1 and events[0]["running_tool"] == "curl https://stuck.example"
    assert alive_before_the_decision == [True]
    assert state.watchdog_terminated
    reason = str(state.watchdog_reason)
    assert "the Manager decided to restart the stalled turn" in reason
    assert fatal_error_is_idle_termination(reason)
    assert idle_termination_running_tool(reason) == "curl https://stuck.example"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_a_long_busy_tool_is_never_reported_or_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A command that computes for longer than the stall limit, printing nothing, is progress."""
    monkeypatch.setattr(run_exec_module, "CPU_SAMPLE_SECONDS", 0.5)
    monkeypatch.setattr(run_exec_module, "CPU_PROGRESS_SECONDS", 0.1)
    events: list[dict] = []
    watch = _watch(tmp_path, events, stall_seconds=2)
    busy = "import time\nend = time.monotonic() + 6\nwhile time.monotonic() < end: pass\n"

    state, process = _run_turn("python train.py", busy, watch, stall_seconds=2)

    assert events == []
    assert not state.watchdog_terminated
    assert process.returncode == 0
    assert any(json.loads(line).get("type") == "turn.completed" for line in state.stdout_lines)
