from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import _needed_for_live_progress
from argus_skill.agent_cli import _sandbox_commands as sandbox_commands
from argus_skill.agent_cli import agent_cli_runner as runner_mod
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_OPENCODE
from argus_skill.core.token_usage import extract_token_usage


def _runner() -> AgentCliRunner:
    return AgentCliRunner(agent_bin="opencode", backend=BACKEND_OPENCODE)


def test_opencode_command_uses_json_stdin_and_resume() -> None:
    command = _runner()._build_opencode_command(
        resume_thread_id="ses-123",
        options=RunnerOptions(
            model="openai/gpt-5.4",
            reasoning_effort="high",
            working_dir="/repo",
            dangerous_yolo=True,
        ),
    )

    assert Path(command[0]).name == "opencode"
    assert command[1:] == [
        "run",
        "--format",
        "json",
        "--model",
        "openai/gpt-5.4",
        "--variant",
        "high",
        "--dir",
        "/repo",
        "--agent",
        "argus-full-access",
        "--session",
        "ses-123",
    ]


def test_opencode_full_auto_uses_explicit_full_access_agent() -> None:
    runner = _runner()
    options = RunnerOptions(full_auto=True)

    child_env = runner._child_env(options)

    assert child_env is not None
    config = json.loads(child_env["OPENCODE_CONFIG_CONTENT"])
    assert config["agent"]["argus-full-access"]["permission"] == {"*": "allow"}


@pytest.mark.parametrize("reasoning_effort", [None, "high", "max", "xhigh"])
def test_opencode_ox_alpha_uses_stable_low_variant(reasoning_effort) -> None:
    command = _runner()._build_opencode_command(
        resume_thread_id=None,
        options=RunnerOptions(
            model="opencode/x-preview-f-free",
            reasoning_effort=reasoning_effort,
        ),
    )

    assert command[command.index("--variant") + 1] == "low"


def test_opencode_no_tools_uses_restricted_agent_without_sandbox() -> None:
    child_env = _runner()._child_env(RunnerOptions(disable_tools=True))

    assert child_env is not None
    config = json.loads(child_env["OPENCODE_CONFIG_CONTENT"])
    assert config["agent"]["argus-no-tools"]["permission"] == {"*": "deny"}


def test_opencode_defers_bare_model_to_its_own_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Still dropped — ``opencode run --model`` only accepts ``provider/id`` —
    but no longer SILENTLY. Dropping it means the operator's configured model
    knob is a no-op and OpenCode quietly runs its own default instead, which is
    indistinguishable from Argus honouring the setting."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_OPENCODE_PROVIDER", raising=False)
    sandbox_commands.reset_unqualified_model_warnings()

    with caplog.at_level("WARNING"):
        command = _runner()._build_opencode_command(
            resume_thread_id=None,
            options=RunnerOptions(model="gpt-5.5"),
        )

    assert "--model" not in command
    assert "ARGUS_SKILL_OPENCODE_PROVIDER" in caplog.text


def test_opencode_bare_model_uses_configured_provider_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric with ``ARGUS_SKILL_PI_PROVIDER``: naming the provider makes the
    operator's model knob actually reach OpenCode."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_OPENCODE_PROVIDER", "deepseek")

    command = _runner()._build_opencode_command(
        resume_thread_id=None,
        options=RunnerOptions(model="deepseek-chat"),
    )

    assert command[command.index("--model") + 1] == "deepseek/deepseek-chat"


def test_opencode_read_only_uses_restricted_agent_and_strips_override() -> None:
    runner = _runner()
    options = RunnerOptions(
        sandbox_mode="read-only",
        full_auto=True,
        extra_args=["--agent", "build", "--pure"],
    )
    command = runner._build_opencode_command(
        resume_thread_id=None,
        options=options,
    )

    assert command.count("--agent") == 1
    assert command[command.index("--agent") + 1] == "argus-read-only"
    assert "--dangerously-skip-permissions" not in command
    assert "--pure" in command
    child_env = runner._child_env(options)
    assert child_env is not None
    config = json.loads(child_env["OPENCODE_CONFIG_CONTENT"])
    permission = config["agent"]["argus-read-only"]["permission"]
    assert permission == {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
    }


def test_opencode_delivers_plain_prompt_on_stdin() -> None:
    command = _runner()._build_opencode_command(
        resume_thread_id=None,
        options=RunnerOptions(),
    )

    prepared, stdin_prompt, cleanup_path = _runner()._prepare_prompt_delivery(
        command, "review"
    )

    assert prepared == command
    assert stdin_prompt == "review"
    assert cleanup_path is None
    assert "--output-schema" not in command


def test_opencode_event_consumer_tracks_session_text_and_completion() -> None:
    from argus_skill.agent_cli._event_consumers import _OpenCodeWriteState

    runner = _runner()
    messages: list[str] = []
    write_state = _OpenCodeWriteState()
    state = runner._consume_opencode_event(
        event={
            "type": "text",
            "sessionID": "ses-123",
            "part": {"text": "done"},
        },
        thread_id=None,
        agent_messages=messages,
        write_state=write_state,
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )
    state = runner._consume_opencode_event(
        event={
            "type": "step_finish",
            "sessionID": "ses-123",
            "part": {"reason": "stop"},
        },
        thread_id=state[0],
        agent_messages=messages,
        write_state=write_state,
        turn_completed=state[1],
        turn_failed=state[2],
        fatal_error=state[3],
    )

    assert messages == ["done"]
    assert state == ("ses-123", True, False, None)


def test_opencode_tool_step_is_not_terminal() -> None:
    from argus_skill.agent_cli._event_consumers import _OpenCodeWriteState

    state = _runner()._consume_opencode_event(
        event={"type": "step_finish", "part": {"reason": "tool-calls"}},
        thread_id=None,
        agent_messages=[],
        write_state=_OpenCodeWriteState(),
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == (None, False, False, None)


@pytest.mark.parametrize("reason", ["length", "content-filter", "error", "unknown"])
def test_opencode_non_success_finish_reasons_fail_closed(reason: str) -> None:
    from argus_skill.agent_cli._event_consumers import _OpenCodeWriteState

    state = _runner()._consume_opencode_event(
        event={"type": "step_finish", "part": {"reason": reason}},
        thread_id=None,
        agent_messages=[],
        write_state=_OpenCodeWriteState(),
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == (None, False, True, f"OpenCode runner reported {reason}.")


class _FakeStdin:
    def __init__(self) -> None:
        self.text = ""

    def write(self, text: str) -> None:
        self.text += text

    def close(self) -> None:
        return None


class _FakeProcess:
    def __init__(self, stdout_lines: list[str]) -> None:
        self.stdout = iter(stdout_lines)
        self.stderr = iter([])
        self.stdin = _FakeStdin()
        self.returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self.returncode


def _export_payload(*, message_id: str) -> str:
    return json.dumps(
        {
            "messages": [
                {
                    "info": {
                        "id": "user-1",
                        "role": "user",
                        "sessionID": "ses-123",
                    },
                    "parts": [{"id": "user-part", "type": "text", "text": "prompt"}],
                },
                {
                    "info": {
                        "id": message_id,
                        "role": "assistant",
                        "sessionID": "ses-123",
                        "finish": "stop",
                        "cost": 0.002,
                        "tokens": {
                            "input": 10,
                            "output": 2,
                            "reasoning": 1,
                            "cache": {"read": 5, "write": 0},
                        },
                    },
                    "parts": [
                        {
                            "id": "step-start-1",
                            "messageID": message_id,
                            "sessionID": "ses-123",
                            "type": "step-start",
                        },
                        {
                            "id": "text-1",
                            "messageID": message_id,
                            "sessionID": "ses-123",
                            "type": "text",
                            "text": "OK",
                        },
                        {
                            "id": "step-finish-1",
                            "messageID": message_id,
                            "sessionID": "ses-123",
                            "type": "step-finish",
                            "reason": "stop",
                            "cost": 0.002,
                            "tokens": {
                                "input": 10,
                                "output": 2,
                                "reasoning": 1,
                                "cache": {"read": 5, "write": 0},
                            },
                        },
                    ],
                },
            ]
        }
    )


def test_opencode_recovers_completed_turn_when_json_stream_ends_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step_start = {
        "type": "step_start",
        "sessionID": "ses-123",
        "part": {
            "id": "step-start-1",
            "messageID": "assistant-1",
            "sessionID": "ses-123",
            "type": "step-start",
        },
    }
    process = _FakeProcess([json.dumps(step_start)])
    run_kwargs: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        run_kwargs.update(kwargs)
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_export_payload(message_id="assistant-1"),
            stderr="",
        )

    monkeypatch.setattr(runner_mod.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        AgentCliRunner,
        "_resolve_executable",
        staticmethod(lambda value: value),
    )

    streamed: list[str] = []
    result = _runner().run_exec(
        prompt="Reply with exactly OK.",
        resume_thread_id=None,
        options=RunnerOptions(on_agent_message=streamed.append),
        run_label="opencode-recovery-test",
    )
    usage = extract_token_usage(result.json_events)

    assert process.stdin.text == "Reply with exactly OK.\n"
    assert result.thread_id == "ses-123"
    assert result.last_agent_message == "OK"
    assert streamed == ["OK"]
    assert result.turn_completed is True
    assert result.turn_failed is False
    assert result.fatal_error is None
    assert usage.source == "per_step"
    assert usage.input_tokens == 15
    assert usage.output_tokens == 2
    assert usage.provider_cost_usd == 0.002
    assert run_kwargs["encoding"] == "utf-8"
    assert run_kwargs["errors"] == "replace"


def test_opencode_export_recovery_rejects_stale_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_mod.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=_export_payload(message_id="old-assistant"),
            stderr="",
        ),
    )
    events, error = _runner()._recover_opencode_events(
        thread_id="ses-123",
        observed_events=[
            {
                "type": "step_start",
                "part": {"id": "step-start-1", "messageID": "current-assistant"},
            }
        ],
        options=RunnerOptions(),
    )

    assert events == []
    assert error == "OpenCode session export did not contain the current assistant message."


def test_opencode_recovers_from_database_when_export_is_truncated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "opencode.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            (
                "assistant-1",
                "ses-123",
                1,
                json.dumps(
                    {
                        "role": "assistant",
                        "finish": "stop",
                        "cost": 0.002,
                        "tokens": {
                            "input": 10,
                            "output": 2,
                            "reasoning": 1,
                            "cache": {"read": 5, "write": 0},
                        },
                    }
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "text-1",
                    "assistant-1",
                    "ses-123",
                    2,
                    json.dumps({"type": "text", "text": "OK"}),
                ),
                (
                    "finish-1",
                    "assistant-1",
                    "ses-123",
                    3,
                    json.dumps({"type": "step-finish", "reason": "stop"}),
                ),
            ],
        )

    call_kwargs: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        call_kwargs.append(kwargs)
        if len(call_kwargs) == 1:
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout='{"messages":[{"info":{"role":"assistant"',
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=f"{database}\n",
            stderr="",
        )

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)

    events, error = _runner()._recover_opencode_events(
        thread_id="ses-123",
        observed_events=[
            {
                "type": "step_start",
                "part": {"id": "step-start-1", "messageID": "assistant-1"},
            }
        ],
        options=RunnerOptions(),
    )

    assert error is None
    assert [event["type"] for event in events] == ["text", "step_finish"]
    assert events[0]["part"]["text"] == "OK"
    assert events[1]["part"]["reason"] == "stop"
    assert len(call_kwargs) == 2
    assert all(kwargs["encoding"] == "utf-8" for kwargs in call_kwargs)
    assert all(kwargs["errors"] == "replace" for kwargs in call_kwargs)


def test_opencode_nested_error_is_preserved() -> None:
    from argus_skill.agent_cli._event_consumers import _OpenCodeWriteState

    state = _runner()._consume_opencode_event(
        event={
            "type": "error",
            "sessionID": "ses-error",
            "error": {"data": {"message": "provider unavailable"}},
        },
        thread_id=None,
        agent_messages=[],
        write_state=_OpenCodeWriteState(),
        turn_completed=False,
        turn_failed=False,
        fatal_error=None,
    )

    assert state == ("ses-error", False, True, "provider unavailable")


def test_opencode_step_events_count_as_live_progress() -> None:
    for event_type in ("step_start", "reasoning", "step_finish"):
        line = json.dumps({"type": event_type, "part": {}})
        assert _needed_for_live_progress("main.stdout", line)


def test_opencode_step_usage_is_summed() -> None:
    usage = extract_token_usage([
        {
            "type": "step_finish",
            "part": {
                "tokens": {
                    "input": 49,
                    "output": 34,
                    "reasoning": 14,
                    "cache": {"read": 20096, "write": 0},
                },
                "cost": 0.012,
            },
        },
        {
            "type": "step_finish",
            "part": {
                "tokens": {
                    "input": 123,
                    "output": 17,
                    "reasoning": 3,
                    "cache": {"read": 20096, "write": 0},
                },
                "cost": 0.003,
            },
        },
    ])

    assert usage.source == "per_step"
    assert usage.input_tokens == 40364
    assert usage.cached_input_tokens == 40192
    assert usage.cache_write_tokens == 0
    assert usage.output_tokens == 51
    assert usage.reasoning_output_tokens == 17
    assert usage.provider_cost_usd == 0.015
