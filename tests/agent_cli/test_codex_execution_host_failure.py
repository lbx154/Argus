"""Trusted Codex startup failures survive completion and a clean process exit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend._result import UsageAccumulator, translate_result
from argus_skill.agent_cli._event_consumers import EventConsumerMixin
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions

HOST_ERROR = (
    "Code Mode is unavailable because failed to spawn code-mode host "
    r"C:\Users\USER\Codex\bin\VERSION\codex-code-mode-host.exe: "
    "host executable was not found. Code mode will fail closed; enable "
    "`features.code_mode_host` and install `codex-code-mode-host`."
)


def _item_error(message: str = HOST_ERROR) -> dict:
    return {"type": "item.completed", "item": {"type": "error", "message": message}}


def _replay(events: list[dict], **kwargs):
    state = (None, False, False, None)
    messages: list[str] = []
    for event in events:
        state = EventConsumerMixin._consume_codex_event(
            event=event,
            thread_id=state[0],
            agent_messages=messages,
            turn_completed=state[1],
            turn_failed=state[2],
            fatal_error=state[3],
            **kwargs,
        )
    return state, messages


def test_nested_host_failure_survives_completion_and_later_errors():
    state, _ = _replay([
        {"type": "thread.started", "thread_id": "host-failure"},
        _item_error(),
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "turn.failed", "error": {"message": "stream disconnected"}},
        {"type": "turn.completed"},
    ])
    assert state == ("host-failure", True, True, HOST_ERROR)


@pytest.mark.parametrize("event", [
    {"type": "item.completed", "item": {"type": "agent_message", "text": HOST_ERROR}},
    _item_error("Reconnecting... 1/5"),
    _item_error("WebSocket failed; falling back to HTTPS transport"),
    _item_error("A command returned exit status 1"),
    _item_error("The documentation says: " + HOST_ERROR),
])
def test_successful_controls_do_not_become_fatal(event):
    state, _ = _replay([event, {"type": "turn.completed"}])
    assert state[1:] == (True, False, None)


def test_tool_free_call_can_complete_without_execution_host():
    state, _ = _replay([_item_error(), {"type": "turn.completed"}], disable_tools=True)
    assert state[1:] == (True, False, None)


def test_explicit_failure_still_fails_when_tools_are_disabled():
    state, _ = _replay([
        _item_error(),
        {"type": "turn.failed", "error": {"message": "provider rejected request"}},
    ], disable_tools=True)
    assert state[2:] == (True, "provider rejected request")


@pytest.mark.integration
@pytest.mark.parametrize("disable_tools", [False, True])
def test_fake_cli_error_then_completion_then_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disable_tools: bool,
):
    events = [
        {"type": "thread.started", "thread_id": "fake-cli"},
        _item_error(),
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": "MILESTONE_STATUS=done\nNEXT_OWNER=reviewer",
        }},
        {"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 7}},
    ]
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, sys\nsys.stdin.read()\n"
        "print('HTTP 401 recovered earlier', file=sys.stderr, flush=True)\n"
        f"events = json.loads({json.dumps(json.dumps(events))})\n"
        "for event in events:\n    print(json.dumps(event), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        AgentCliRunner, "_build_command", lambda self, **kwargs: [sys.executable, str(script)],
    )
    result = AgentCliRunner(agent_bin=sys.executable, backend="codex").run_exec(
        prompt="offline regression",
        resume_thread_id=None,
        options=RunnerOptions(disable_tools=disable_tools, working_dir=str(tmp_path)),
        run_label="host-regression",
    )
    assert result.exit_code == 0
    assert result.turn_completed
    assert result.turn_failed is (not disable_tools)
    assert result.fatal_error == (None if disable_tools else HOST_ERROR)
    assert result.agent_messages == [events[2]["item"]["text"]]
    assert result.stderr_lines == ["HTTP 401 recovered earlier"]
    translated = translate_result(
        result, resume_thread_id=None, copilot_usage=None, usage_accumulator=UsageAccumulator(),
    )
    assert translated.stop_kind == (None if disable_tools else "backend_unavailable")
    assert translated.fatal_error == (None if disable_tools else HOST_ERROR)
    assert translated.input_tokens == 11
    assert translated.output_tokens == 7
