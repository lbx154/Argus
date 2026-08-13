from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_GROK
from argus_skill.core.token_usage import extract_token_usage


def _runner(agent_bin: str = "grok") -> AgentCliRunner:
    return AgentCliRunner(agent_bin=agent_bin, backend=BACKEND_GROK)


def test_grok_command_uses_headless_messages_stream_and_resume() -> None:
    command = _runner()._build_grok_command(
        resume_thread_id="019fd194-c77f-7556-90c3-b7cbc25bc1c6",
        options=RunnerOptions(
            model="grok-build",
            reasoning_effort="xhigh",
            working_dir="/repo",
            dangerous_yolo=True,
        ),
    )

    assert Path(command[0]).name == "grok"
    assert command[1:] == [
        "--no-auto-update",
        "--output-format",
        "streaming-messages-json",
        "--verbatim",
        "--cwd",
        "/repo",
        "--model",
        "grok-build",
        "--reasoning-effort",
        "xhigh",
        "--yolo",
        "--resume",
        "019fd194-c77f-7556-90c3-b7cbc25bc1c6",
    ]


def test_grok_read_only_restricts_tools_and_strips_overrides() -> None:
    command = _runner()._build_grok_command(
        resume_thread_id=None,
        options=RunnerOptions(
            sandbox_mode="read-only",
            full_auto=True,
            extra_args=["--tools", "run_terminal_cmd,search_replace", "--max-turns", "3"],
        ),
    )

    assert command.count("--tools") == 1
    assert command[command.index("--tools") + 1] == "read_file,grep,list_dir"
    assert "--yolo" not in command
    assert command[-2:] == ["--max-turns", "3"]


def test_grok_prompt_uses_private_temporary_file() -> None:
    command = _runner()._build_grok_command(
        resume_thread_id=None,
        options=RunnerOptions(),
    )
    prompt = "review\n" + "x" * 200_000

    prepared, stdin_prompt, cleanup_path = _runner()._prepare_prompt_delivery(
        command, prompt
    )
    try:
        assert stdin_prompt is None
        assert cleanup_path is not None
        assert prepared[-2:] == ["--prompt-file", str(cleanup_path)]
        assert prompt not in prepared
        assert cleanup_path.read_text(encoding="utf-8") == prompt
        if os.name != "nt":
            assert cleanup_path.stat().st_mode & 0o077 == 0
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def test_grok_messages_stream_tracks_session_text_and_completion() -> None:
    runner = _runner()
    messages: list[str] = []
    state = runner._consume_event(
        event={
            "type": "system",
            "subtype": "init",
            "session_id": "grok-session",
            "model": "grok-build",
        },
        thread_id=None,
        agent_messages=messages,
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )
    state = runner._consume_event(
        event={
            "type": "assistant",
            "session_id": "grok-session",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
            },
        },
        thread_id=state[0],
        agent_messages=messages,
        turn_completed=state[1],
        turn_failed=state[2],
        fatal_error=state[3],
    )
    state = runner._consume_event(
        event={
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "grok-session",
            "result": "done",
            "stop_reason": "end_turn",
        },
        thread_id=state[0],
        agent_messages=messages,
        turn_completed=state[1],
        turn_failed=state[2],
        fatal_error=state[3],
    )

    assert messages == ["done"]
    assert state == ("grok-session", True, False, None)


def test_grok_error_result_fails_closed() -> None:
    state = _runner()._consume_event(
        event={
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "session_id": "grok-session",
            "result": "authentication failed",
        },
        thread_id=None,
        agent_messages=[],
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == ("grok-session", False, True, "authentication failed")


def test_grok_non_terminal_stop_reason_fails_closed() -> None:
    state = _runner()._consume_event(
        event={
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "grok-session",
            "result": "partial",
            "stop_reason": "max_tokens",
        },
        thread_id=None,
        agent_messages=[],
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == (
        "grok-session",
        False,
        True,
        "Grok Build stopped with max_tokens.",
    )


def test_grok_result_usage_is_accounted() -> None:
    usage = extract_token_usage(
        [
            {
                "type": "result",
                "subtype": "success",
                "usage": {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 400,
                    "cache_creation_input_tokens": 10,
                    "output_tokens": 25,
                    "reasoning_tokens": 7,
                },
                "total_cost_usd": 0.03,
            }
        ]
    )

    assert usage.input_tokens == 100
    assert usage.cached_input_tokens == 400
    assert usage.cache_write_tokens == 10
    assert usage.output_tokens == 25
    assert usage.reasoning_output_tokens == 7
    assert usage.provider_cost_usd == pytest.approx(0.03)


def test_grok_run_exec_removes_prompt_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "grok.py"
    script.write_text(
        """import json
import pathlib
import sys

args = sys.argv[1:]
prompt_path = pathlib.Path(args[args.index("--prompt-file") + 1])
assert prompt_path.read_text(encoding="utf-8") == "hello from Argus"
print(json.dumps({"type": "system", "subtype": "init", "session_id": "sid"}))
print(json.dumps({"type": "assistant", "session_id": "sid", "message": {
    "role": "assistant", "content": [{"type": "text", "text": "hello"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "session_id": "sid", "result": "hello",
                  "stop_reason": "end_turn"}))
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        executable = tmp_path / "grok.cmd"
        monkeypatch.setenv("ARGUS_TEST_PYTHON", sys.executable)
        executable.write_text(
            '@"%ARGUS_TEST_PYTHON%" "%~dp0grok.py" %*\n',
            encoding="ascii",
        )
    else:
        executable = tmp_path / "grok"
        executable.write_text(
            f"#!/bin/sh\nexec {sys.executable!r} {str(script)!r} \"$@\"\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    result = _runner(str(executable)).run_exec(
        prompt="hello from Argus",
        resume_thread_id=None,
        options=RunnerOptions(),
    )

    prompt_path = Path(result.command[result.command.index("--prompt-file") + 1])
    assert result.turn_completed
    assert result.thread_id == "sid"
    assert result.last_agent_message == "hello"
    assert not prompt_path.exists()


def test_isolated_grok_call_fails_closed() -> None:
    with pytest.raises(ValueError, match="isolated Grok calls are not supported"):
        _runner()._build_grok_command(
            resume_thread_id=None,
            options=RunnerOptions(isolate_workdir=True),
        )
