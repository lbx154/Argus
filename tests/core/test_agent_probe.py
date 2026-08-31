from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from argus_skill.core.agent_probe import (
    run_agent_repair_prompt,
    run_read_only_agent_prompt,
)


def test_agent_probe_runs_read_only_without_mutating_safe_mode(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    class Runner:
        def __init__(self, *, backend: str, runner_bin: str, **kwargs) -> None:
            calls["executable"] = runner_bin
            calls["backend"] = backend
            calls["runner_defaults"] = kwargs

        def run_exec(self, **kwargs):
            calls.update(kwargs)
            assert kwargs["options"].sandbox_mode == "read-only"
            assert kwargs["options"].force_safe_mode is True
            assert kwargs["options"].disable_tools is False
            assert kwargs["options"].model is None
            return SimpleNamespace(
                exit_code=0,
                last_agent_message="ARGUS_SETUP_OK",
                agent_messages=["ARGUS_SETUP_OK"],
                fatal_error="",
                stderr_lines=[],
            )

    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.AgentCliBackend",
        Runner,
    )
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "0")

    result = run_read_only_agent_prompt(
        backend="claude",
        executable="/usr/bin/claude",
        prompt="reply exactly",
        run_label="setup-smoke",
    )

    assert result.ok is True
    assert result.output == "ARGUS_SETUP_OK"
    assert calls["backend"] == "claude"
    assert calls["run_label"] == "setup-smoke"
    assert __import__("os").environ["ARGUS_SKILL_SAFE_MODE"] == "0"


def test_agent_probe_surfaces_runner_failure(monkeypatch) -> None:
    class Runner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_exec(self, **_kwargs):
            return SimpleNamespace(
                exit_code=1,
                turn_completed=False,
                last_agent_message="",
                agent_messages=[],
                fatal_error="authentication required",
                stderr_lines=[],
            )

    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.AgentCliBackend",
        Runner,
    )

    result = run_read_only_agent_prompt(
        backend="claude",
        executable="/usr/bin/claude",
        prompt="reply exactly",
        run_label="setup-smoke",
    )

    assert result.ok is False
    assert result.error == "authentication required"


def test_agent_probe_surfaces_spawn_error_without_traceback(monkeypatch) -> None:
    class Runner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_exec(self, **_kwargs):
            raise OSError("executable could not start")

    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.AgentCliBackend",
        Runner,
    )

    result = run_read_only_agent_prompt(
        backend="claude",
        executable="/usr/bin/claude",
        prompt="reply exactly",
        run_label="setup-smoke",
    )

    assert result.ok is False
    assert result.error == "OSError: executable could not start"


def test_agent_probe_allows_read_only_tool_activity(monkeypatch) -> None:
    class Runner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_exec(self, **_kwargs):
            return SimpleNamespace(
                exit_code=0,
                turn_completed=True,
                tool_activity_observed=True,
                last_agent_message="ARGUS_SETUP_OK",
                agent_messages=["ARGUS_SETUP_OK"],
                fatal_error="",
                stderr_lines=[],
            )

    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.AgentCliBackend",
        Runner,
    )

    result = run_read_only_agent_prompt(
        backend="claude",
        executable="/usr/bin/claude",
        prompt="reply exactly",
        run_label="setup-smoke",
    )

    assert result.ok is True
    assert result.tool_activity_observed is True


def test_agent_repair_prompt_enables_tools_and_real_workdir(
    monkeypatch,
    tmp_path,
) -> None:
    calls: dict[str, object] = {}

    class Runner:
        def __init__(self, *, backend: str, runner_bin: str, **kwargs) -> None:
            calls["backend"] = backend
            calls["executable"] = runner_bin
            calls["runner_defaults"] = kwargs

        def run_exec(self, **kwargs):
            calls.update(kwargs)
            options = kwargs["options"]
            assert options.working_dir == str(tmp_path)
            assert options.add_dirs == [str(tmp_path / "argus-home")]
            assert options.dangerous_yolo is True
            assert options.full_auto is True
            assert options.sandbox_mode is None
            return SimpleNamespace(
                exit_code=0,
                tool_activity_observed=True,
                last_agent_message="Fixed config and verified Doctor.",
                agent_messages=["Fixed config and verified Doctor."],
                fatal_error="",
                stderr_lines=[],
            )

    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.AgentCliBackend",
        Runner,
    )

    result = run_agent_repair_prompt(
        backend="codex",
        executable="/usr/bin/codex",
        prompt="repair Argus",
        working_dir=tmp_path,
        add_dirs=(tmp_path / "argus-home",),
        known_secret_values=("secret-value",),
    )

    assert result.ok is True
    assert result.output == "Fixed config and verified Doctor."
    assert calls["run_label"] == "doctor-repair"
    assert calls["runner_defaults"]["known_secret_values_override"] == (
        "secret-value",
    )


def test_agent_repair_prompt_requires_real_tool_activity(
    monkeypatch,
    tmp_path,
) -> None:
    class Runner:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_exec(self, **_kwargs):
            return SimpleNamespace(
                exit_code=0,
                tool_activity_observed=False,
                last_agent_message="Run these commands yourself.",
                agent_messages=["Run these commands yourself."],
                fatal_error="",
                stderr_lines=[],
            )

    monkeypatch.setattr(
        "argus_skill.adapters.agent_cli_backend.AgentCliBackend",
        Runner,
    )

    result = run_agent_repair_prompt(
        backend="claude",
        executable="/usr/bin/claude",
        prompt="repair Argus",
        working_dir=tmp_path,
    )

    assert result.ok is False
    assert result.error == "Agent returned without inspecting or repairing with tools"


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("claude", ("--tools", "")),
        ("copilot", ("--available-tools=__argus_no_tools__",)),
        ("opencode", ("--agent", "argus-no-tools")),
        ("pi", ("--no-tools",)),
        ("grok", ("--tools", "")),
    ],
)
def test_supported_doctor_backends_disable_all_tools(
    backend: str,
    expected: tuple[str, ...],
) -> None:
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from argus_skill.agent_cli.runner_backend import normalize_runner_backend

    command = AgentCliRunner(
        backend,
        backend=normalize_runner_backend(backend),
    )._build_command(
        resume_thread_id=None,
        options=RunnerOptions(
            sandbox_mode="read-only",
            force_safe_mode=True,
            disable_tools=True,
        ),
    )

    for item in expected:
        assert item in command


def test_opencode_tool_free_agent_denies_every_tool() -> None:
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from argus_skill.agent_cli.runner_backend import BACKEND_OPENCODE

    runner = AgentCliRunner("opencode", backend=BACKEND_OPENCODE)
    env = runner._child_env(RunnerOptions(
        sandbox_mode="read-only",
        force_safe_mode=True,
        disable_tools=True,
    ))
    assert env is not None
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])

    assert config["agent"]["argus-no-tools"]["permission"] == {"*": "deny"}
