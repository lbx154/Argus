from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import _needed_for_live_progress
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_PI
from argus_skill.core.codex_usage import extract_token_usage


def _runner() -> AgentCliRunner:
    return AgentCliRunner(agent_bin="pi", backend=BACKEND_PI)


def test_pi_command_uses_json_stdin_and_exact_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    command = _runner()._build_pi_command(
        resume_thread_id="019fd194-c77f-7556-90c3-b7cbc25bc1c6",
        options=RunnerOptions(
            model="github-copilot/gpt-5.6-sol",
            reasoning_effort="max",
            working_dir="/repo",
            dangerous_yolo=True,
        ),
    )

    assert Path(command[0]).name == "pi"
    assert command[1:] == [
        "--mode",
        "json",
        "--session-dir",
        str((tmp_path / "argus-home" / "pi-sessions").resolve()),
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--model",
        "github-copilot/gpt-5.6-sol",
        "--thinking",
        "max",
        "--session",
        "019fd194-c77f-7556-90c3-b7cbc25bc1c6",
    ]


def test_pi_bare_model_uses_configured_provider_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_PI_PROVIDER", raising=False)

    command = _runner()._build_pi_command(
        resume_thread_id=None,
        options=RunnerOptions(model="gpt-5.6-sol"),
    )

    assert command[command.index("--model") + 1] == (
        "github-copilot/gpt-5.6-sol"
    )

    monkeypatch.setenv("ARGUS_SKILL_PI_PROVIDER", "openai")
    command = _runner()._build_pi_command(
        resume_thread_id=None,
        options=RunnerOptions(model="gpt-5.6-sol"),
    )
    assert command[command.index("--model") + 1] == "openai/gpt-5.6-sol"


def test_pi_read_only_allows_only_builtin_read_tools_and_strips_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    runner = _runner()
    options = RunnerOptions(
        sandbox_mode="read-only",
        extra_args=[
            "--tools",
            "bash,write",
            "--approve",
            "--extension",
            "dangerous.ts",
            "--verbose",
        ],
    )

    command = runner._build_pi_command(resume_thread_id=None, options=options)

    assert command.count("--tools") == 1
    assert command[command.index("--tools") + 1] == "read,grep,find,ls"
    assert "--approve" not in command
    assert "--extension" not in command
    assert "dangerous.ts" not in command
    assert "--verbose" in command
    child_env = runner._child_env(options)
    assert child_env is not None
    assert child_env["PI_SKIP_VERSION_CHECK"] == "1"
    assert child_env["PI_TELEMETRY"] == "0"


def test_isolated_pi_call_is_ephemeral_and_cannot_resume() -> None:
    runner = _runner()
    command = runner._build_pi_command(
        resume_thread_id=None,
        options=RunnerOptions(isolate_workdir=True),
    )
    assert "--no-session" in command
    assert "--session-dir" not in command

    with pytest.raises(ValueError, match="cannot resume"):
        runner._build_pi_command(
            resume_thread_id="old-session",
            options=RunnerOptions(isolate_workdir=True),
        )


def test_pi_event_consumer_tracks_session_text_usage_model_and_completion() -> None:
    runner = _runner()
    messages: list[str] = []
    state = runner._consume_pi_event(
        event={"type": "session", "id": "pi-session"},
        thread_id=None,
        agent_messages=messages,
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )
    state = runner._consume_pi_event(
        event={
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "stopReason": "stop",
            },
        },
        thread_id=state[0],
        agent_messages=messages,
        turn_completed=state[1],
        turn_failed=state[2],
        fatal_error=state[3],
    )
    state = runner._consume_pi_event(
        event={"type": "agent_settled"},
        thread_id=state[0],
        agent_messages=messages,
        turn_completed=state[1],
        turn_failed=state[2],
        fatal_error=state[3],
    )

    assert messages == ["done"]
    assert state == ("pi-session", True, False, None)


def test_pi_error_message_fails_closed() -> None:
    state = _runner()._consume_pi_event(
        event={
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "stopReason": "error",
                "errorMessage": "provider unavailable",
            },
        },
        thread_id="pi-session",
        agent_messages=[],
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == ("pi-session", False, True, "provider unavailable")


def test_pi_message_usage_is_summed_without_double_counting_turn_end() -> None:
    events = [
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "model": "gpt-5.6-sol",
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 400,
                    "cacheWrite": 10,
                    "reasoning": 7,
                    "cost": {"total": 0.12},
                },
            },
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "model": "gpt-5.6-sol",
                "usage": {
                    "input": 50,
                    "output": 5,
                    "cacheRead": 200,
                    "cacheWrite": 0,
                    "reasoning": 2,
                    "cost": {"total": 0.03},
                },
            },
        },
    ]

    usage = extract_token_usage(events)

    assert usage.source == "pi_message"
    assert usage.input_tokens == 760
    assert usage.cached_input_tokens == 600
    assert usage.cache_write_tokens == 10
    assert usage.output_tokens == 25
    assert usage.reasoning_output_tokens == 9
    assert usage.provider_cost_usd == pytest.approx(0.15)


def test_pi_tool_and_message_events_count_as_live_progress() -> None:
    for event_type in ("message_end", "tool_execution_start", "tool_execution_end"):
        line = json.dumps({"type": event_type})
        assert _needed_for_live_progress("main.stdout", line)
