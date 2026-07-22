from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from argus_skill.agent_cli._idle_watchdog import (
    STALLED_STAGE,
    TERMINATE_STAGE,
    WARNING_STAGE,
    IdleEscalation,
)
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions


def test_idle_escalation_emits_once_and_resets_on_activity() -> None:
    escalation = IdleEscalation(
        warning_seconds=10,
        stalled_seconds=30,
        terminate_seconds=45,
    )

    assert escalation.newly_due(9) == ()
    assert escalation.newly_due(10) == (WARNING_STAGE,)
    assert escalation.newly_due(29) == ()
    assert escalation.newly_due(30) == (STALLED_STAGE,)
    assert escalation.newly_due(45) == (TERMINATE_STAGE,)
    assert escalation.newly_due(100) == ()

    escalation.reset()
    assert escalation.newly_due(30) == (WARNING_STAGE, STALLED_STAGE)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
def test_hard_idle_terminates_only_current_model_process_group() -> None:
    events: list[tuple[str, str]] = []
    runner = AgentCliRunner(
        agent_bin=sys.executable,
        event_callback=lambda stream, line: events.append((stream, line)),
    )
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    durable_job = subprocess.Popen(sleeper, start_new_session=True)
    model_call = subprocess.Popen(
        sleeper,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        started = time.monotonic()
        state = runner._stream_turn_output(
            process=model_call,
            command=sleeper,
            options=RunnerOptions(watchdog_hard_idle_seconds=1),
            run_label="test-watchdog",
            thread_id=None,
        )

        assert time.monotonic() - started < 5
        assert state.watchdog_terminated is True
        assert "hard idle timeout" in str(state.watchdog_reason).lower()
        assert model_call.poll() is not None
        assert durable_job.poll() is None
        assert any("hard idle timeout" in line.lower() for _stream, line in events)
    finally:
        if model_call.poll() is None:
            model_call.terminate()
            model_call.wait(timeout=3)
        if durable_job.poll() is None:
            durable_job.terminate()
            durable_job.wait(timeout=3)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
def test_provider_exit_cleans_descendants_before_waiting_for_pipe_eof() -> None:
    runner = AgentCliRunner(agent_bin=sys.executable)
    command = [
        sys.executable,
        "-c",
        (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', "
            "\"import time; time.sleep(30)\"])"
        ),
    ]
    provider = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started = time.monotonic()

    state = runner._stream_turn_output(
        process=provider,
        command=command,
        options=RunnerOptions(watchdog_hard_idle_seconds=10),
        run_label="test-orphan-cleanup",
        thread_id=None,
    )

    assert time.monotonic() - started < 5
    assert state.orphan_process_group_id == provider.pid
    assert state.orphan_process_group_cleanup_succeeded is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group isolation")
def test_provider_exit_does_not_wait_for_separate_owned_process_pipes() -> None:
    runner = AgentCliRunner(agent_bin=sys.executable)
    command = [
        "bash",
        "-c",
        "setsid sleep 30 & echo $!",
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
        child_pid = int(state.stdout_lines[-1])

        assert time.monotonic() - started < 3
        assert state.orphan_process_group_id == 0
        os.kill(child_pid, 0)
    finally:
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
