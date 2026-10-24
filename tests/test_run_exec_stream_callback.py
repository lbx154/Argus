"""The live-streaming hook in the vendored runner: ``run_exec`` fires
``RunnerOptions.on_agent_message`` with each NEW assistant block the instant it
lands on stdout, so a front-end can stream the reply instead of waiting for the
whole turn. Opt-in — a ``None`` callback leaves the turn byte-for-byte unchanged.

This drives the real ``AgentCliRunner.run_exec`` with a faked copilot CLI process
(no binary, no network): two ``assistant.message`` blocks then a ``result``.
"""

from __future__ import annotations

import json

import pytest

from argus.agent_cli import _run_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import BACKEND_CLAUDE, BACKEND_COPILOT


class _FakeStdin:
    def write(self, _s):  # copilot path only closes stdin
        return None

    def close(self):
        return None


class _FakeProc:
    """Minimal stand-in for a copilot subprocess: yields preset stdout lines,
    empty stderr, and reports a clean exit. ``poll()`` returning 0 is safe — the
    run_exec loop only breaks once BOTH pipe sentinels have drained."""

    def __init__(self, stdout_lines: list[str]) -> None:
        self.stdout = iter(stdout_lines)  # 'for line in pipe' consumes an iterator
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):  # noqa: ARG002
        self.returncode = 0
        return 0


@pytest.fixture()
def _fake_copilot(monkeypatch: pytest.MonkeyPatch):
    lines = [
        json.dumps({"type": "assistant.message", "data": {"content": "block one"}}),
        json.dumps({"type": "assistant.message", "data": {"content": "block two"}}),
        json.dumps({"type": "result", "sessionId": "sess-1", "exitCode": 0}),
    ]
    popen_kwargs: dict[str, object] = {}

    def _popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return _FakeProc(lines)

    monkeypatch.setattr(_run_exec, "spawn_owned_process", _popen)
    # Don't require a real copilot binary on PATH.
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda x: x))
    return popen_kwargs


@pytest.mark.parametrize("exit_code", [0, 1])
def test_copilot_model_response_waits_for_authoritative_result(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
) -> None:
    final = "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer"
    lines = [
        json.dumps({"type": "assistant.message", "data": {"content": final}}),
        json.dumps(
            {
                "type": "model.response",
                "data": {
                    "kind": "response",
                    "response": {
                        "content": final,
                        "responses_message_status": "completed",
                        "phase": "final_answer",
                    },
                },
            }
        ),
        json.dumps({"type": "result", "sessionId": "sess-final", "exitCode": exit_code}),
    ]
    process = _FakeProc(lines)

    monkeypatch.setattr(_run_exec, "spawn_owned_process", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["copilot", "-p"],
    )

    def terminate(proc, *, include_detached_children=False):  # noqa: ARG001
        raise AssertionError("model responses must not terminate the provider")

    monkeypatch.setattr(AgentCliRunner, "_terminate_process", staticmethod(terminate))

    result = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
    ).run_exec(
        prompt="finish",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="engineer-r1",
    )

    assert result.thread_id == "sess-final"
    assert result.turn_completed is (exit_code == 0)
    assert result.turn_failed is (exit_code != 0)
    assert result.agent_messages == [final]


def test_claude_stream_records_tool_activity_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "session_id": "claude-session",
                "message": {
                    "model": "glm-5.2",
                    "content": [
                        {"type": "text", "text": "Inspecting the source."},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Read",
                            "input": {"file_path": "src/app.py"},
                        },
                    ],
                    "usage": {"input_tokens": 120, "output_tokens": 15},
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "claude-session",
                "result": "done",
            }
        ),
    ]
    monkeypatch.setattr(
        _run_exec,
        "spawn_owned_process",
        lambda *args, **kwargs: _FakeProc(lines),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["claude", "-p"],
    )

    result = AgentCliRunner(
        agent_bin="claude",
        backend=BACKEND_CLAUDE,
    ).run_exec(
        prompt="fix it",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="planner.cycle0",
    )

    assert result.turn_completed is True
    assert result.tool_activity_observed is True
    assert result.usage_model == "glm-5.2"
    assert result.agent_messages == ["Inspecting the source.", "done"]


def test_callback_exception_never_breaks_the_turn(_fake_copilot, monkeypatch) -> None:
    """A raising UI callback is swallowed — the reply must not be lost to a
    front-end bug."""
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **_kw: ["copilot", "-p", "x"]
    )
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)

    def _boom(_block):
        raise RuntimeError("ui exploded")

    result = runner.run_exec(
        prompt="你好",
        resume_thread_id=None,
        options=RunnerOptions(on_agent_message=_boom),
        run_label="stream-test",
    )
    assert result.agent_messages == ["block one", "block two"]
    assert result.exit_code == 0


def test_runner_retains_bounded_stream_tail_with_exact_counts(monkeypatch) -> None:
    lines = [
        json.dumps(
            {
                "type": "assistant.message_delta",
                "data": {"messageId": "m1", "deltaContent": str(index)},
            }
        )
        for index in range(20)
    ]
    lines.extend(
        [
            json.dumps(
                {
                    "type": "assistant.message",
                    "data": {"messageId": "m1", "content": "final"},
                }
            ),
            json.dumps({"type": "result", "sessionId": "sess-1", "exitCode": 0}),
        ]
    )

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_CAPTURE_STDOUT_LINES", "3")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_CAPTURE_JSON_EVENTS", "4")
    monkeypatch.setattr(
        _run_exec,
        "spawn_owned_process",
        lambda *args, **kwargs: _FakeProc(lines),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        AgentCliRunner,
        "_build_command",
        lambda self, **kwargs: ["copilot", "-p", "x"],
    )

    result = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
    ).run_exec(
        prompt="bounded",
        resume_thread_id=None,
        options=RunnerOptions(),
        run_label="stream-test",
    )

    assert result.stdout_line_count == len(lines)
    assert result.json_event_count == len(lines)
    assert len(result.stdout_lines) == 3
    assert [event["type"] for event in result.json_events] == [
        "assistant.message",
        "result",
    ]
    assert result.last_agent_message == "final"
    assert result.thread_id == "sess-1"


def test_runner_keeps_usage_bearing_delta_for_accounting() -> None:
    assert (
        AgentCliRunner.retain_json_event(
            {
                "type": "assistant.message_delta",
                "data": {"deltaContent": "x", "inputTokens": 17},
            }
        )
        is True
    )
    assert (
        AgentCliRunner.retain_json_event(
            {
                "type": "assistant.message_delta",
                "data": {"deltaContent": "x"},
            }
        )
        is False
    )
