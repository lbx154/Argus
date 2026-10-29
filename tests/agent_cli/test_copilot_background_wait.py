"""Copilot's empty reply while awaiting a native shell is not a final handoff."""
from __future__ import annotations

import json

import pytest

from argus.agent_cli import _env, _run_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import BACKEND_COPILOT
from argus.core.runner_receipts import is_provider_background_wait_receipt


class _Stdin:
    def write(self, _text):
        return None

    def close(self):
        return None


class _Process:
    def __init__(self, events, exit_code=0):
        self.stdout = iter(json.dumps(event) + "\n" for event in events)
        self.stderr = iter([])
        self.stdin = _Stdin()
        self.returncode = exit_code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _message(content, phase=None, **extra):
    data = {"content": content}
    if phase is not None:
        data["phase"] = phase
    return {"type": "assistant.message", "data": data, **extra}


def _tool_complete(state="active", **extra):
    return {
        "type": "tool.execution_complete",
        "data": {
            "toolCallId": "read-shell-2",
            "success": True,
            "result": {"content": "native shell status"},
            "toolTelemetry": {"properties": {"read_target_state": state}},
        },
        **extra,
    }


def _run(monkeypatch, events, exit_code=0):
    monkeypatch.setattr(_env, "_CAPTURE_JSON_EVENTS", 1)
    process = _Process(events, exit_code)
    monkeypatch.setattr(_run_exec, "spawn_owned_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        AgentCliRunner, "_resolve_executable", staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **kwargs: ["copilot", "-p"],
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    return runner.run_exec(
        prompt="finish the scientific comparison", resume_thread_id=None,
        options=RunnerOptions(), run_label="engineer-r1",
    )


@pytest.mark.parametrize("content,state", [
    ("Comparison finished; the optional drawing can remain in the background.", "active"),
    ("", "completed"),
])
def test_real_final_or_completed_tool_work_keeps_normal_completion(monkeypatch, content, state):
    result = _run(monkeypatch, [
        _message("Earlier progress"), _tool_complete(), _tool_complete(state),
        _message(content, "final_answer"),
        {"type": "result", "sessionId": "complete-parent", "exitCode": 0},
    ])
    assert result.turn_completed and not result.turn_failed
    assert result.fatal_error is None
    assert result.last_agent_message == content


def test_native_delegate_cannot_supply_or_clear_parent_completion(monkeypatch):
    result = _run(monkeypatch, [
        _message("Parent progress"), _tool_complete(),
        _message("", "final_answer"),
        _tool_complete("completed", agentId="figure-worker"),
        _message("Figure ready", "final_answer", agentId="figure-worker"),
        {"type": "result", "agentId": "figure-worker", "sessionId": "child", "exitCode": 0},
        {"type": "result", "sessionId": "parent", "exitCode": 0},
    ])
    assert result.thread_id == "parent"
    assert result.last_agent_message == ""
    assert "Figure ready" not in result.agent_messages
    assert is_provider_background_wait_receipt(result.fatal_error)
