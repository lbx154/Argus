"""Unit tests for the Grok Build backend (no live model calls)."""
from __future__ import annotations

from pathlib import Path

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import (
    BACKEND_GROK,
    normalize_runner_backend,
    resolve_runner_bin,
)
from argus_skill.core.backend_readiness import (
    AUTH_MODE_SUBSCRIPTION,
    check_backend_readiness,
)
from argus_skill.core.role_config import resolve_role_config


def test_normalize_grok_aliases() -> None:
    assert normalize_runner_backend("grok") == BACKEND_GROK
    assert normalize_runner_backend("grok-build") == BACKEND_GROK
    assert normalize_runner_backend("grok_build") == BACKEND_GROK


def test_grok_runner_resolves_standard_install_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / ".grok" / "bin" / "grok"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert resolve_runner_bin(BACKEND_GROK) == str(executable)
    assert AgentCliRunner(backend=BACKEND_GROK).agent_bin == str(executable)


def test_build_grok_command_yolo_resume_model(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "grok"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    runner = AgentCliRunner(backend=BACKEND_GROK, agent_bin=str(executable))
    command = runner._build_command(
        resume_thread_id="sess-123",
        options=RunnerOptions(
            model="grok-4.5",
            reasoning_effort="high",
            dangerous_yolo=True,
            working_dir="/tmp/work",
        ),
    )
    assert command[0] == str(executable)
    assert "--output-format" in command
    assert "streaming-messages-json" in command
    assert command[command.index("--model") + 1] == "grok-4.5"
    assert command[command.index("--reasoning-effort") + 1] == "high"
    assert command[command.index("--cwd") + 1] == "/tmp/work"
    assert command[command.index("--permission-mode") + 1] == "bypassPermissions"
    assert command[command.index("--resume") + 1] == "sess-123"
    # Prompt is attached later via --prompt-file, not as -p argv.
    assert "-p" not in command
    assert "--prompt-file" not in command


def test_build_grok_command_read_only_tools(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "grok"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    runner = AgentCliRunner(backend=BACKEND_GROK, agent_bin=str(executable))
    command = runner._build_command(
        resume_thread_id=None,
        options=RunnerOptions(sandbox_mode="read-only"),
    )
    assert command[command.index("--tools") + 1] == "read_file,grep,list_dir"
    assert "--permission-mode" not in command


def test_prepare_prompt_delivery_uses_prompt_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "grok"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    runner = AgentCliRunner(backend=BACKEND_GROK, agent_bin=str(executable))
    base = runner._build_command(
        resume_thread_id=None,
        options=RunnerOptions(dangerous_yolo=True),
    )
    command, stdin_prompt = runner._prepare_prompt_delivery(base, "hello grok")
    assert stdin_prompt is None
    assert "--prompt-file" in command
    path = Path(command[command.index("--prompt-file") + 1])
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "hello grok\n"
    runner._cleanup_ephemeral_prompt_files()
    assert not path.exists()


def test_consume_grok_streaming_messages_events() -> None:
    """Replay a real Grok streaming-messages-json shape without calling the API."""
    runner = AgentCliRunner(backend=BACKEND_GROK, agent_bin="grok")
    thread_id = None
    messages: list[str] = []
    completed = False
    failed = False
    fatal = None

    events = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "019fe9a5-5469-73e2-b510-aeb31687e62b",
        },
        {
            "type": "assistant",
            "session_id": "019fe9a5-5469-73e2-b510-aeb31687e62b",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "OK"}],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": "019fe9a5-5469-73e2-b510-aeb31687e62b",
        },
    ]
    for event in events:
        thread_id, completed, failed, fatal = runner._consume_event(
            event=event,
            thread_id=thread_id,
            agent_messages=messages,
            turn_completed=completed,
            turn_failed=failed,
            fatal_error=fatal,
        )

    assert thread_id == "019fe9a5-5469-73e2-b510-aeb31687e62b"
    assert completed is True
    assert failed is False
    assert fatal is None
    assert messages[-1].strip() == "OK"


def test_grok_backend_display_label() -> None:
    env = {"ARGUS_SKILL_LIFE_BACKEND": "grok"}
    config = resolve_role_config("manager", env=env)
    assert config.backend == "grok"
    assert config.backend_label == "Grok Build"


def test_check_backend_readiness_grok_mocked(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "grok"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    def fake_run_text(command, *, timeout_s, input_text=None):
        import subprocess

        argv = list(command)
        class Result:
            def __init__(self, code: int, out: str, err: str = "") -> None:
                self.returncode = code
                self.stdout = out
                self.stderr = err

        if "--version" in argv:
            return Result(0, "grok 1.0.0 (deadbeef)\n")
        if "models" in argv:
            return Result(
                0,
                "You are logged in with grok.com.\n\nDefault model: grok-4.5\n",
            )
        return Result(1, "", "unexpected")

    monkeypatch.setattr(
        "argus_skill.core.backend_readiness._run_text",
        fake_run_text,
    )
    report = check_backend_readiness(
        "grok",
        AUTH_MODE_SUBSCRIPTION,
        probe_auth=True,
        env={"PATH": str(tmp_path), "HOME": str(tmp_path)},
    )
    assert report.ok, report.problems
    assert report.profile.backend == "grok"
    assert report.version == "1.0.0"
    assert report.auth_checked is True
