"""Prompt delivery under real input/output backpressure."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.agent_cli import _run_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions


def run_fixture(monkeypatch, tmp_path, body, prompt, *, options=None, callback=None):
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex", event_callback=callback)
    monkeypatch.setattr(
        runner, "_build_command", lambda **kw: [sys.executable, "-X", "utf8", "-u", "-c", body]
    )
    processes = []
    original = _run_exec.subprocess.Popen

    def capture(*args, **kwargs):
        proc = original(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(_run_exec.subprocess, "Popen", capture)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            runner.run_exec,
            prompt=prompt,
            resume_thread_id=None,
            options=options or RunnerOptions(working_dir=str(tmp_path)),
            run_label="engineer-r1",
        )
        try:
            result = future.result(timeout=5)
            return result, time.monotonic() - start, processes
        finally:
            for proc in processes:
                runner._terminate_process(proc)
            # If an assertion or timeout fired, terminating the identified root
            # releases a blocked stdin write before joining the executor.
            if not future.done():
                future.result(timeout=5)


def test_prompt_reaches_a_cli_that_requires_piped_stdin(monkeypatch, tmp_path):
    """The deployed copilot shim reads a prompt only from FIFO stdin.

    The VS Code copilotCLIShim answers "No prompt provided" when stdin is a
    regular file, which took down every one-shot copilot turn fleet-wide on
    2026-09-08. Delivery must present the prompt through a real pipe.
    """
    received = tmp_path / "received.txt"
    body = (
        "import os,stat,sys,json;from pathlib import Path;"
        "assert stat.S_ISFIFO(os.fstat(0).st_mode), 'stdin must be a pipe';"
        f"Path({str(received)!r}).write_text(sys.stdin.read());"
        "print(json.dumps({'type':'turn.completed'}),flush=True)"
    )
    result, elapsed, _processes = run_fixture(monkeypatch, tmp_path, body, "hello shim")
    assert received.read_text() == "hello shim\n"
    assert result.turn_completed and result.exit_code == 0 and elapsed < 3


@pytest.mark.parametrize("reason", ["stop", "hard_idle"])
def test_child_not_reading_input_can_be_stopped(monkeypatch, tmp_path, reason):
    start = time.monotonic()
    options = RunnerOptions(working_dir=str(tmp_path), watchdog_hard_idle_seconds=1)
    if reason == "stop":
        options.external_interrupt_reason_provider = lambda: (
            "daemon stop requested" if time.monotonic() - start > 0.6 else None
        )
    result, elapsed, processes = run_fixture(
        monkeypatch, tmp_path, "import time;time.sleep(20)", "p" * 262144, options=options
    )
    assert elapsed < 3 and result.turn_failed
    assert (
        "daemon stop requested" if reason == "stop" else "hard idle timeout"
    ) in result.fatal_error
    assert all(p.poll() is not None for p in processes)
