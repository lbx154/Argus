"""dsh (DeepSeek Harness) backend: argv shape, env mapping, prompt delivery,
and the fail-closed completion synthesis in ``_finalize_turn_result``.

dsh's headless profile emits no JSON event stream — it prints the final
assistant text once and exits 0 on a completed turn — so the interesting
surface to pin down is the command/env construction and the finalize
branch, not an event consumer.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.agent_cli._run_exec import _StreamState
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import (
    BACKEND_DSH,
)


def _runner(agent_bin: str = "dsh") -> AgentCliRunner:
    return AgentCliRunner(agent_bin=agent_bin, backend=BACKEND_DSH)


def _overlay_path() -> Path:
    from argus.agent_cli._sandbox_commands import _dsh_overlay_patch_path

    return Path(_dsh_overlay_patch_path())


# ---------------------------------------------------------------- registration


def test_dsh_overlay_patch_resource_exists_and_targets_model() -> None:
    overlay = _overlay_path()
    assert overlay.is_file()
    text = overlay.read_text(encoding="utf-8")
    assert "id: agent-default-model" in text
    assert "ARGUS_DSH_MODEL" in text
    assert "ARGUS_DSH_PROVIDER" in text
    assert "ARGUS_DSH_DISABLE_TOOLS" in text
    assert "id: tool-subagent-fork" in text
    assert "id: tool-workflow" in text


# ---------------------------------------------------------------------- command


def test_dsh_read_only_strips_permission_overrides() -> None:
    command = _runner()._build_dsh_command(
        resume_thread_id=None,
        options=RunnerOptions(
            sandbox_mode="read-only",
            extra_args=["--profile", "sneaky", "--permission-mode", "bypassPermissions"],
        ),
    )

    assert command[command.index("--profile") + 1] == "headless"
    assert "--permission-mode" not in command
    assert "bypassPermissions" not in command


# ------------------------------------------------------------- prompt delivery


def test_dsh_oversized_prompt_uses_workspace_mission_file(tmp_path: Path) -> None:
    runner = _runner()
    command = runner._build_dsh_command(resume_thread_id=None, options=RunnerOptions())
    prompt = "review\n" + "x" * 200_000

    prepared, stdin_prompt, cleanup_path = runner._prepare_prompt_delivery(
        command, prompt, working_dir=str(tmp_path)
    )

    try:
        assert stdin_prompt is None
        assert cleanup_path is not None
        assert cleanup_path.parent == tmp_path
        assert cleanup_path.name.startswith(".argus-dsh-prompt-")
        assert cleanup_path.read_text(encoding="utf-8") == prompt
        assert str(cleanup_path) in prepared[-1]
        assert prompt not in prepared
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


# ---------------------------------------------------------------- child env


def test_dsh_env_maps_model_and_permission_mode() -> None:
    from argus.agent_cli._prompt_delivery import _apply_dsh_env

    env = _apply_dsh_env(
        {"PATH": "/usr/bin"},
        RunnerOptions(model="deepseek-v4-pro"),
        agent_bin="/tools/node/bin/dsh",
    )
    assert env["ARGUS_DSH_MODEL"] == "deepseek-v4-pro"
    assert "ARGUS_DSH_PROVIDER" not in env
    assert env["DSH_PERMISSION_MODE"] == "danger-full-access"
    agent_dir = str(Path("/tools/node/bin/dsh").expanduser().resolve().parent)
    assert env["PATH"].split(os.pathsep) == [agent_dir, "/usr/bin"]


def test_dsh_env_splits_qualified_model_and_read_only() -> None:
    from argus.agent_cli._prompt_delivery import _apply_dsh_env

    env = _apply_dsh_env(
        {"PATH": "/usr/bin"},
        RunnerOptions(model="third-party/model-x", sandbox_mode="read-only"),
        agent_bin="/usr/bin/dsh",
    )
    assert env["ARGUS_DSH_PROVIDER"] == "third-party"
    assert env["ARGUS_DSH_MODEL"] == "model-x"
    assert env["DSH_PERMISSION_MODE"] == "read-only"
    assert "ARGUS_DSH_DISABLE_TOOLS" not in env


def test_dsh_env_disables_tools_for_tool_free_calls() -> None:
    from argus.agent_cli._prompt_delivery import _apply_dsh_env

    env = _apply_dsh_env(
        {"PATH": "/usr/bin"},
        RunnerOptions(disable_tools=True),
        agent_bin="/usr/bin/dsh",
    )
    assert env["ARGUS_DSH_DISABLE_TOOLS"] == "1"


def test_dsh_resolves_beside_real_node_when_only_node_shim_is_on_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    require_symlink_support,
) -> None:
    from argus.agent_cli import runner_backend

    prefix_bin = tmp_path / "node-prefix" / "bin"
    shim_bin = tmp_path / "shims"
    prefix_bin.mkdir(parents=True)
    shim_bin.mkdir()
    # Windows launchers carry PATHEXT suffixes: node.exe and npm's dsh.cmd.
    windows = os.name == "nt"
    node = prefix_bin / ("node.exe" if windows else "node")
    dsh = prefix_bin / ("dsh.cmd" if windows else "dsh")
    node.write_text("", encoding="utf-8")
    dsh.write_text("", encoding="utf-8")
    node.chmod(0o755)
    dsh.chmod(0o755)
    (shim_bin / node.name).symlink_to(node)
    monkeypatch.setenv("PATH", str(shim_bin))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert runner_backend.resolve_runner_bin(BACKEND_DSH) == str(dsh)


# ----------------------------------------------------------- finalize branch


def _state(
    *,
    stdout_lines: list[str],
    stderr_lines: list[str],
) -> _StreamState:
    state = _StreamState(thread_id=None)
    state.stdout_lines.extend(stdout_lines)
    state.stderr_lines.extend(stderr_lines)
    state.stdout_line_count = len(stdout_lines)
    state.stderr_line_count = len(stderr_lines)
    return state


@pytest.mark.parametrize(("stdout", "stderr", "exit_code", "completed", "messages", "error"), [
    pytest.param(["", "  final answer  ", ""], [], 0, False, ["final answer"], None, id="stdout-completion"),
    pytest.param([""], [], 0, False, [], "no assistant output", id="empty-output"),
    pytest.param([], ["dsh: MISSING_CREDENTIAL: llm-deepseek: no API key"], 1, False, [], "MISSING_CREDENTIAL", id="nonzero-exit"),
    pytest.param(["legacy"], [], 0, True, ["legacy"], None, id="already-completed"),
])
def test_dsh_finalize_terminal_receipts(stdout, stderr, exit_code, completed, messages, error) -> None:
    state = _state(stdout_lines=stdout, stderr_lines=stderr)
    if completed:
        state.turn_completed = True
        state.agent_messages = list(messages)
    result = _runner()._finalize_turn_result(
        process=SimpleNamespace(returncode=exit_code), command=["dsh"], options=RunnerOptions(), state=state,
    )
    assert result.turn_completed is (error is None)
    assert result.turn_failed is (error is not None)
    assert result.agent_messages == messages
    assert result.thread_id is None
    if error:
        assert error in (result.fatal_error or "")
    else:
        assert result.fatal_error is None
