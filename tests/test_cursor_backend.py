from __future__ import annotations

import subprocess

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import (
    BACKEND_CURSOR,
    SUPPORTED_BACKENDS,
    default_runner_bin,
    normalize_runner_backend,
)
from argus_skill.core import backend_readiness


def test_cursor_is_a_first_class_backend() -> None:
    assert "cursor" in SUPPORTED_BACKENDS
    assert normalize_runner_backend("CURSOR") == BACKEND_CURSOR
    assert default_runner_bin(BACKEND_CURSOR) == "agent"


def test_cursor_command_uses_stdin_stream_json_and_resume() -> None:
    runner = AgentCliRunner("agent", backend=BACKEND_CURSOR)
    command = runner._build_command(
        resume_thread_id="session-42",
        options=RunnerOptions(
            model="composer-1",
            reasoning_effort="high",
            working_dir="C:/work",
            add_dirs=["C:/shared"],
            plugin_dirs=["C:/plugin"],
        ),
    )
    assert command[:4] == ["agent", "-p", "--output-format", "stream-json"]
    assert ["--model", "composer-1[effort=high]"] == command[4:6]
    assert "--workspace" in command and "C:/work" in command
    assert "--add-dir" in command and "C:/shared" in command
    assert "--plugin-dir" in command and "C:/plugin" in command
    assert command[-2:] == ["--resume", "session-42"]


def test_cursor_read_only_cannot_be_broadened_by_extra_args() -> None:
    runner = AgentCliRunner("agent", backend=BACKEND_CURSOR)
    command = runner._build_command(
        resume_thread_id=None,
        options=RunnerOptions(
            sandbox_mode="read-only",
            extra_args=["--force", "--mode", "agent", "--add-dir", "C:/escape", "--plan"],
        ),
    )
    assert command[-2:] == ["--mode", "ask"] or ["--mode", "ask"] == command[4:6]
    assert "--force" not in command
    assert command.count("agent") == 1
    assert "C:/escape" not in command
    assert "--plan" in command


def test_cursor_event_stream_completes_and_captures_session() -> None:
    runner = AgentCliRunner("agent", backend=BACKEND_CURSOR)
    messages: list[str] = []
    state = runner._consume_event(
        event={"type": "assistant", "session_id": "s1", "message": {"content": [{"type": "text", "text": "hello"}]}},
        thread_id=None, agent_messages=messages, turn_completed=False,
        turn_failed=False, fatal_error=None,
    )
    state = runner._consume_event(
        event={"type": "result", "subtype": "success", "session_id": "s1", "is_error": False, "result": "hello"},
        thread_id=state[0], agent_messages=messages, turn_completed=state[1],
        turn_failed=state[2], fatal_error=state[3],
    )
    assert state == ("s1", True, False, None)
    assert messages == ["hello"]


def test_cursor_auth_rejects_zero_exit_not_logged_in(monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(
        backend_readiness,
        "_run_text",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "Not logged in\n", ""),
    )
    ok, detail = backend_readiness._probe_cli_auth("cursor", "agent", timeout_s=1)
    assert not ok
    assert "Not logged in" in detail


def test_cursor_api_key_and_install_commands(monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    assert backend_readiness._probe_cli_auth(
        "cursor", "agent", timeout_s=1, env={"CURSOR_API_KEY": "test-only"}
    ) == (True, "")
    assert "cursor.com/install" in backend_readiness.backend_install_command("cursor", platform_name="posix")
    assert "win32=true" in backend_readiness.backend_install_command("cursor", platform_name="nt")


def test_cursor_scripted_cli_end_to_end(tmp_path) -> None:
    import os

    if os.name == "nt":
        executable = tmp_path / "agent.cmd"
        executable.write_text(
            "@echo off\r\n"
            "more >nul\r\n"
            "echo {\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"cursor-e2e\"}\r\n"
            "echo {\"type\":\"assistant\",\"session_id\":\"cursor-e2e\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"cursor ok\"}]}}\r\n"
            "echo {\"type\":\"result\",\"subtype\":\"success\",\"session_id\":\"cursor-e2e\",\"is_error\":false,\"result\":\"cursor ok\"}\r\n",
            encoding="utf-8",
        )
    else:
        executable = tmp_path / "agent"
        executable.write_text(
            "#!/bin/sh\ncat >/dev/null\n"
            "printf '%s\\n' "
            "'{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"cursor-e2e\"}' "
            "'{\"type\":\"assistant\",\"session_id\":\"cursor-e2e\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"cursor ok\"}]}}' "
            "'{\"type\":\"result\",\"subtype\":\"success\",\"session_id\":\"cursor-e2e\",\"is_error\":false,\"result\":\"cursor ok\"}'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    result = AgentCliRunner(str(executable), backend=BACKEND_CURSOR).run_exec(
        prompt="say hello",
        resume_thread_id=None,
        options=RunnerOptions(),
    )

    assert result.exit_code == 0
    assert result.thread_id == "cursor-e2e"
    assert result.turn_completed and not result.turn_failed
    assert result.last_agent_message == "cursor ok"
