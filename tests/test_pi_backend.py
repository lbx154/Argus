from __future__ import annotations

from pathlib import Path

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import BACKEND_PI


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

    assert Path(command[0]).stem.casefold() == "pi"
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


def test_pi_loads_only_explicit_role_skill_paths(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    command = _runner()._build_pi_command(
        resume_thread_id=None,
        options=RunnerOptions(skill_paths=[str(skill_dir)]),
    )

    assert "--no-skills" in command
    assert command[command.index("--skill") + 1] == str(skill_dir)


def test_pi_bare_model_uses_configured_provider_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The knob still exists: two authenticated catalogs can carry the same id
    (``claude-opus-5`` on both ``anthropic`` and a Copilot proxy), and naming
    the provider is how an operator disambiguates."""
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("ARGUS_SKILL_PI_PROVIDER", "deepseek")

    command = _runner()._build_pi_command(
        resume_thread_id=None,
        options=RunnerOptions(model="deepseek-chat"),
    )
    assert command[command.index("--model") + 1] == "deepseek/deepseek-chat"

    # An already-qualified id is never re-qualified.
    command = _runner()._build_pi_command(
        resume_thread_id=None,
        options=RunnerOptions(model="anthropic/claude-opus-5"),
    )
    assert command[command.index("--model") + 1] == "anthropic/claude-opus-5"


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
