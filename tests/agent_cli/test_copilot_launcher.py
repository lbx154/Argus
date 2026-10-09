from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.copilot_launcher import stable_copilot_command
from argus.agent_cli.runner_backend import resolve_runner_bin
from argus.engineer.round_stop_signals import (
    backend_failure_cause,
    infrastructure_failure_review_decision,
)


@pytest.mark.skipif(os.name == "nt", reason="VS Code POSIX shell launcher")
@pytest.mark.integration
def test_launcher_rewrite_during_real_child_execution_cannot_change_exit_status(tmp_path):
    directory = tmp_path / "copilotCli"
    directory.mkdir()
    child = directory / "copilotCLIShim.js"
    child.write_text("import sys\nprint('ready', flush=True)\nsys.stdin.readline()\nprint(sys.argv[1:])\n")
    wrapper = directory / "copilot"
    wrapper.write_text(
        '#!/bin/sh\nunset NODE_OPTIONS\n'
        f'ELECTRON_RUN_AS_NODE=1 {shlex.quote(sys.executable)} {shlex.quote(str(child))} "$@"\n'
    )
    wrapper.chmod(0o700)
    command = stable_copilot_command([str(wrapper), "--output-format", "json"])
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        wrapper.write_text('#!/bin/sh\nexit 127\n--output-format\n')
        output, error = process.communicate("continue\n", timeout=5)
        assert process.returncode == 0
        assert "--output-format" in output
        assert not error
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="VS Code POSIX shell launcher")
def test_incomplete_launcher_fails_before_start(tmp_path):
    wrapper = tmp_path / "copilotCli" / "copilot"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\nunset NODE_OPTIONS\n")
    with pytest.raises(RuntimeError, match="incomplete"):
        stable_copilot_command([str(wrapper)])


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable resolution")
def test_implicit_resolution_prefers_standalone_but_explicit_launcher_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    shim = tmp_path / "copilotCli" / "copilot"
    native = tmp_path / ".local" / "bin" / "copilot"
    for path in [shim, native]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o700)
    monkeypatch.setenv("PATH", str(shim.parent))
    assert resolve_runner_bin("copilot") == str(native)
    assert resolve_runner_bin("copilot", str(shim)) == str(shim)


@pytest.mark.integration
def test_real_process_exit_after_success_keeps_output_and_reports_actual_failure(tmp_path, monkeypatch):
    script = tmp_path / "provider.py"
    messages = [
        {"type": "assistant.message", "data": {"content": "Saved three reviewed source notes."}},
        {"type": "result", "sessionId": "test-session", "exitCode": 0},
    ]
    script.write_text(
        "import sys,time\n"
        + "\n".join(f"print({json.dumps(json.dumps(m))}, flush=True)" for m in messages)
        + "\ntime.sleep(0.1)\nprint('copilot: 3: --output-format: not found', file=sys.stderr, flush=True)\nsys.exit(127)\n"
    )
    runner = AgentCliRunner(agent_bin=sys.executable, backend="copilot")
    monkeypatch.setattr(runner, "_build_command", lambda **kwargs: [sys.executable, "-u", str(script)])
    result = runner.run_exec(prompt="test", resume_thread_id=None,
                             options=RunnerOptions(working_dir=str(tmp_path)), run_label="engineer-r1")
    assert result.exit_code == 127
    assert result.turn_completed
    assert result.agent_messages == ["Saved three reviewed source notes."]
    assert "after model turn completion" in result.fatal_error
    assert "--output-format: not found" in result.fatal_error
    cause = backend_failure_cause(result.fatal_error, exit_code=result.exit_code)
    assert cause.kind == "cli_exit_after_output"
    decision = infrastructure_failure_review_decision(cause=cause, fatal_error=result.fatal_error, exit_code=127)
    assert "Preserve that output" in decision.reason
    assert "could not be started" not in decision.reason
