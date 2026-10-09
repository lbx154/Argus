"""A turn the CLI closed with its own result event is not undone by the process exit code."""
from __future__ import annotations

import json

from argus.agent_cli import _run_exec
from argus.agent_cli._env import _CAPTURE_JSON_EVENTS_ENV
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import BACKEND_COPILOT


class _Stdin:
    def write(self, _text):
        return None

    def close(self):
        return None


class _Process:
    def __init__(self, events, exit_code, stderr):
        self.stdout = iter(json.dumps(event) + "\n" for event in events)
        self.stderr = iter(line + "\n" for line in stderr)
        self.stdin = _Stdin()
        self.returncode = exit_code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _run(monkeypatch, events, *, exit_code, stderr=()):
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "0")
    monkeypatch.setenv(_CAPTURE_JSON_EVENTS_ENV, "1")
    process = _Process(events, exit_code, list(stderr))
    monkeypatch.setattr(_run_exec, "spawn_owned_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda value: value))
    monkeypatch.setattr(AgentCliRunner, "_build_command", lambda self, **kwargs: ["copilot", "-p"])
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    return runner.run_exec(
        prompt="save the notes", resume_thread_id=None, options=RunnerOptions(), run_label="engineer-r1",
    )


_FINISHED = [
    {"type": "assistant.message", "data": {"content": "Saved three wiki pages.", "phase": "final_answer"}},
    {"type": "result", "sessionId": "s-1", "exitCode": 0, "usage": {"premiumRequests": 1}},
]


def test_completed_result_event_outranks_a_wrapper_exit(monkeypatch):
    """The VS Code shim was rewritten by an update while the 7-minute Engineer
    call ran; the shell then read a stale line and exited 127 after the CLI's
    own result event said exitCode 0. Argus must not call that a missing CLI."""
    result = _run(monkeypatch, _FINISHED, exit_code=127,
                  stderr=["/home/u/.vscode-server/copilotCli/copilot: 3: --output-format: not found"])
    assert result.exit_code == 0
    assert result.turn_completed and not result.turn_failed
    assert result.fatal_error is None
    assert result.thread_id == "s-1"
    assert result.agent_messages == ["Saved three wiki pages."]
    assert any("not found" in line for line in result.stderr_lines)


def test_a_non_zero_exit_without_a_result_event_still_fails(monkeypatch):
    result = _run(monkeypatch, _FINISHED[:1], exit_code=127, stderr=["copilot: not found"])
    assert result.exit_code == 127
    assert result.turn_failed and not result.turn_completed
    assert result.fatal_error
