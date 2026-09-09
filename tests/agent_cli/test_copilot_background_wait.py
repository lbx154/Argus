"""Copilot's empty reply while awaiting a native shell is not a final handoff."""
from __future__ import annotations

import json

import pytest

from argus_skill.agent_cli import _run_exec
from argus_skill.agent_cli._env import _CAPTURE_JSON_EVENTS_ENV
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_COPILOT
from argus_skill.core.runner_receipts import is_provider_background_wait_receipt


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
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "0")
    monkeypatch.setenv(_CAPTURE_JSON_EVENTS_ENV, "1")
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


def test_empty_final_after_native_wait_does_not_promote_old_progress(monkeypatch):
    result = _run(monkeypatch, [
        _message("The positive control passed; I am still running the full comparison."),
        _tool_complete(),
        _message("", "final_answer"),
        {"type": "assistant.idle", "data": {}},
        {"type": "result", "sessionId": "waiting-parent", "exitCode": 0},
    ])

    assert result.exit_code == 0
    assert result.thread_id == "waiting-parent"
    assert result.last_agent_message == ""
    assert not result.turn_completed
    assert is_provider_background_wait_receipt(result.fatal_error)
    # The wait event has fallen out of retained capture; control still knows it.
    assert len(result.json_events) == 1
    assert result.json_events[0]["type"] == "result"


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


def test_nonzero_exit_is_not_reclassified_as_a_background_wait(monkeypatch):
    result = _run(monkeypatch, [
        _tool_complete(), _message("", "final_answer"),
        {"type": "result", "sessionId": "failed-parent", "exitCode": 1},
    ], exit_code=1)
    assert result.turn_failed
    assert not is_provider_background_wait_receipt(result.fatal_error)


def test_review_report_write_can_complete_with_an_empty_final_answer(monkeypatch):
    result = _run(monkeypatch, [
        _message("I will update the review report."),
        {"type": "tool.execution_complete", "data": {
            "toolCallId": "write-review", "success": True,
            "result": {"content": "Review report saved"},
        }},
        _message("", "final_answer"),
        {"type": "result", "sessionId": "reviewer", "exitCode": 0},
    ])
    assert result.turn_completed and result.fatal_error is None
    assert result.last_agent_message == ""
