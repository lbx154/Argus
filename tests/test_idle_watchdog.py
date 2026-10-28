from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
def test_opencode_interrupt_stops_detached_tool_group(tmp_path) -> None:
    runner = AgentCliRunner(agent_bin=sys.executable, backend="opencode")
    child_pid_path = tmp_path / "child.pid"
    completed_path = tmp_path / "completed"
    command = [
        sys.executable,
        "-c",
        (
            "import json, pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-c', "
            f"\"import pathlib, time; time.sleep(2); "
            f"pathlib.Path({str(completed_path)!r}).write_text('done')\"],\n"
            "    start_new_session=True,\n"
            ")\n"
            f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))\n"
            "while True:\n"
            "    print(json.dumps({'type': 'step_start'}), flush=True)\n"
            "    time.sleep(0.05)\n"
        ),
    ]
    model_call = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    child_pid = 0
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not child_pid_path.exists():
            time.sleep(0.02)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))

        state = runner._stream_turn_output(
            process=model_call,
            command=command,
            options=RunnerOptions(
                external_interrupt_reason_provider=lambda: "operator stop",
            ),
            run_label="self-implement",
            thread_id=None,
        )
        time.sleep(2.2)

        assert state.watchdog_terminated is True
        assert "External interrupt: operator stop" in str(state.watchdog_reason)
        assert model_call.poll() is not None
        assert not completed_path.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if model_call.poll() is None:
            model_call.terminate()
            model_call.wait(timeout=3)
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
def test_provider_exit_waits_for_descendants_left_in_its_group(tmp_path) -> None:
    """A descendant left in the provider's group is a job the turn started:
    the turn ends once it has ended, not by killing it (core.turn_jobs)."""
    runner = AgentCliRunner(agent_bin=sys.executable)
    flag = tmp_path / "flag"
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', "
            f"\"import pathlib, time; time.sleep(2); pathlib.Path({str(flag)!r}).write_text('done')\"])"
        ),
    ]
    provider = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    state = runner._stream_turn_output(
        process=provider,
        command=command,
        options=RunnerOptions(watchdog_hard_idle_seconds=10),
        run_label="test-orphan-cleanup",
        thread_id=None,
    )

    assert flag.read_text() == "done"
    assert not state.watchdog_terminated
    assert state.orphan_process_group_id == 0


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process-group isolation")
def test_provider_exit_does_not_wait_for_separate_owned_process_pipes() -> None:
    runner = AgentCliRunner(agent_bin=sys.executable)
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys; "
            "child = subprocess.Popen("
            "[sys.executable, '-c', "
            "'import time; time.sleep(0.5); "
            "[(print(f\"tick-{i}\", flush=True), time.sleep(0.05)) "
            "for i in range(600)]'], "
            "start_new_session=True"
            "); "
            "print(f'CHILD_PID={child.pid}', flush=True)"
        ),
    ]
    provider = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    child_pid = 0
    try:
        started = time.monotonic()
        state = runner._stream_turn_output(
            process=provider,
            command=command,
            options=RunnerOptions(watchdog_hard_idle_seconds=10),
            run_label="test-independent-pipes",
            thread_id=None,
        )
        child_pid = next(
            int(line.removeprefix("CHILD_PID="))
            for line in state.stdout_lines
            if line.startswith("CHILD_PID=")
        )

        # A leaked grandchild pipe blocks until the 30-second child exits.
        # Shared CI runners can take several seconds to schedule the reader
        # shutdown, so keep the bound decisive without treating load as a leak.
        assert time.monotonic() - started < 15
        assert state.orphan_process_group_id == 0
        time.sleep(1)
        os.kill(child_pid, 0)
        reader_prefix = f"argus-provider-pipe-{provider.pid}-"
        assert [
            thread
            for thread in threading.enumerate()
            if thread.name.startswith(reader_prefix)
        ]
    finally:
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        reader_prefix = f"argus-provider-pipe-{provider.pid}-"
        deadline = time.monotonic() + 2
        while (
            any(
                thread.name.startswith(reader_prefix)
                for thread in threading.enumerate()
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert not any(
            thread.name.startswith(reader_prefix)
            for thread in threading.enumerate()
        )


# --- stderr chatter must not silence the idle watchdog ----------------------
#
# Only stdout carries model stream events. A CLI that keeps printing
# diagnostics on stderr (a live_writer ordinal complaint every 20ms, say) used
# to reset the idle clock with every line, and because the queue never went
# empty the deadline checks never even ran. These drive the real streaming
# loop with a Python child that behaves that way.

_CHATTER_PRELUDE = """
import json, sys, time

def chatter(seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        print('ERROR live_writer expected ordinal 23, got 22', file=sys.stderr, flush=True)
        time.sleep(0.02)
"""


def _run_stream_with_chatter(body: str, options: RunnerOptions):
    events: list[tuple[str, str]] = []
    runner = AgentCliRunner(
        agent_bin=sys.executable,
        backend="codex",
        event_callback=lambda stream, line: events.append((stream, line)),
    )
    command = [sys.executable, "-u", "-c", _CHATTER_PRELUDE + body]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        start_new_session=True,
    )
    try:
        state = runner._stream_turn_output(
            process=process,
            command=command,
            options=options,
            run_label="engineer-r1",
            thread_id="stored-thread",
        )
        return state, events
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


def _watchdog_lines(events: list[tuple[str, str]]) -> list[str]:
    return [line for _stream, line in events if line.startswith("[watchdog]")]


def test_stdout_tool_progress_keeps_the_stream_active() -> None:
    event = {
        "type": "item.updated",
        "item": {
            "id": "command-1",
            "type": "command_execution",
            "status": "in_progress",
            "aggregated_output": "tick",
        },
    }
    body = (
        "for _ in range(13):\n"
        f"    print({json.dumps(event)!r}, flush=True)\n"
        "    chatter(0.2)\n"
    )

    state, events = _run_stream_with_chatter(
        body,
        RunnerOptions(watchdog_soft_idle_seconds=1, watchdog_hard_idle_seconds=1),
    )

    assert not state.watchdog_terminated
    assert state.stdout_line_count == 13
    assert not _watchdog_lines(events)
