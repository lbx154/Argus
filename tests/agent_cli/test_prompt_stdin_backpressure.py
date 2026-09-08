"""Prompt delivery under real input/output backpressure."""

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.agent_cli import _run_exec
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions


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


@pytest.mark.parametrize("stream", [1, 2])
@pytest.mark.parametrize("prompt", ["p" * 262144, "中文🙂\n" * 65536], ids=["ascii", "unicode"])
def test_large_prompt_and_startup_output_cannot_deadlock(monkeypatch, tmp_path, stream, prompt):
    received = tmp_path / "received.json"
    body = (
        "import os,sys,json,hashlib;from pathlib import Path;"
        f"os.write({stream},b'x'*262144+b'\\n');data=sys.stdin.read();"
        f"Path({str(received)!r}).write_text(json.dumps({{'hash':hashlib.sha256(data.encode()).hexdigest(),'length':len(data)}}));"
        "print(json.dumps({'type':'turn.completed'}),flush=True)"
    )
    result, elapsed, processes = run_fixture(monkeypatch, tmp_path, body, prompt)
    expected = prompt if prompt.endswith("\n") else prompt + "\n"
    assert json.loads(received.read_text()) == {
        "hash": hashlib.sha256(expected.encode()).hexdigest(),
        "length": len(expected),
    }
    assert result.turn_completed and result.exit_code == 0 and elapsed < 3
    assert all(p.stdin is None for p in processes)


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


def test_spawn_failure_closes_temporary_stdin(monkeypatch, tmp_path):
    inputs = []

    def fail(*args, **kwargs):
        inputs.append(kwargs["stdin"])
        assert kwargs["stdin"].read() == "fixture\n"
        raise OSError("injected spawn error")

    monkeypatch.setattr(_run_exec.subprocess, "Popen", fail)
    runner = AgentCliRunner(agent_bin=sys.executable, backend="codex")
    with pytest.raises(OSError, match="injected spawn"):
        runner.run_exec(
            prompt="fixture",
            resume_thread_id=None,
            options=RunnerOptions(working_dir=str(tmp_path)),
        )
    assert inputs and inputs[0].closed
