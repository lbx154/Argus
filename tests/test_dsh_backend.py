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

import pytest

from argus_skill.agent_cli._run_exec import _StreamState
from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import (
    BACKEND_DSH,
    CLAUDE_FAMILY,
    default_runner_bin,
    normalize_runner_backend,
)
from argus_skill.core.backend_readiness import default_model_for_backend


def _runner(agent_bin: str = "dsh") -> AgentCliRunner:
    return AgentCliRunner(agent_bin=agent_bin, backend=BACKEND_DSH)


def _overlay_path() -> Path:
    from argus_skill.agent_cli._sandbox_commands import _dsh_overlay_patch_path

    return Path(_dsh_overlay_patch_path())


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int,
        stdout_lines: list[str],
        stderr_lines: list[str],
    ) -> None:
        self.returncode = returncode
        self.stdout = iter(stdout_lines)
        self.stderr = iter(stderr_lines)
        self.stdin = None

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self.returncode


# ---------------------------------------------------------------- registration


def test_dsh_registers_as_a_native_backend_not_claude_family() -> None:
    assert normalize_runner_backend("dsh") == BACKEND_DSH
    assert default_runner_bin(BACKEND_DSH) == "dsh"
    assert BACKEND_DSH not in CLAUDE_FAMILY


def test_dsh_adopts_official_v4_flash_when_no_model_was_selected() -> None:
    assert default_model_for_backend(
        BACKEND_DSH,
        env={},
        persisted={},
    ) == "deepseek-official/deepseek-v4-flash"


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


def test_dsh_command_shape_ignores_resume_and_appends_overlay() -> None:
    command = _runner()._build_dsh_command(
        resume_thread_id="session-that-cannot-be-resumed",
        options=RunnerOptions(model="deepseek-v4-pro"),
    )

    assert Path(command[0]).name == "dsh"
    assert command[1:5] == [
        "--profile",
        "headless",
        "--patch",
        str(_overlay_path()),
    ]
    # The headless runner creates a fresh session per boot; a resume id must
    # never be forwarded as argv.
    assert "session-that-cannot-be-resumed" not in command
    assert "--resume" not in command
    assert "--model" not in command


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


def test_dsh_short_prompt_delivers_through_argv() -> None:
    runner = _runner()
    command = runner._build_dsh_command(resume_thread_id=None, options=RunnerOptions())
    prompt = "review this diff"

    prepared, stdin_prompt, cleanup_path = runner._prepare_prompt_delivery(
        command, prompt
    )

    assert stdin_prompt is None
    assert cleanup_path is None
    assert prepared[-1] == prompt


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
    from argus_skill.agent_cli._prompt_delivery import _apply_dsh_env

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
    from argus_skill.agent_cli._prompt_delivery import _apply_dsh_env

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
    from argus_skill.agent_cli._prompt_delivery import _apply_dsh_env

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
    from argus_skill.agent_cli import runner_backend

    prefix_bin = tmp_path / "node-prefix" / "bin"
    shim_bin = tmp_path / "shims"
    prefix_bin.mkdir(parents=True)
    shim_bin.mkdir()
    node = prefix_bin / "node"
    dsh = prefix_bin / "dsh"
    node.write_text("", encoding="utf-8")
    dsh.write_text("", encoding="utf-8")
    node.chmod(0o755)
    dsh.chmod(0o755)
    (shim_bin / "node").symlink_to(node)
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


def test_dsh_finalize_synthesizes_completion_from_stdout() -> None:
    runner = _runner()
    state = _state(
        stdout_lines=["", "  final answer  ", ""],
        stderr_lines=[],
    )
    process = _FakeProcess(returncode=0, stdout_lines=[], stderr_lines=[])

    result = runner._finalize_turn_result(
        process=process,
        command=["dsh"],
        options=RunnerOptions(),
        state=state,
    )

    assert result.turn_completed is True
    assert result.turn_failed is False
    assert result.agent_messages == ["final answer"]
    assert result.thread_id is None


def test_dsh_finalize_empty_output_fails_closed() -> None:
    runner = _runner()
    state = _state(stdout_lines=[""], stderr_lines=[])
    process = _FakeProcess(returncode=0, stdout_lines=[], stderr_lines=[])

    result = runner._finalize_turn_result(
        process=process,
        command=["dsh"],
        options=RunnerOptions(),
        state=state,
    )

    assert result.turn_completed is False
    assert result.turn_failed is True
    assert "no assistant output" in (result.fatal_error or "")


def test_dsh_finalize_nonzero_exit_fails_closed_with_stderr() -> None:
    runner = _runner()
    state = _state(
        stdout_lines=[],
        stderr_lines=["dsh: MISSING_CREDENTIAL: llm-deepseek: no API key"],
    )
    process = _FakeProcess(returncode=1, stdout_lines=[], stderr_lines=[])

    result = runner._finalize_turn_result(
        process=process,
        command=["dsh"],
        options=RunnerOptions(),
        state=state,
    )

    assert result.turn_completed is False
    assert result.turn_failed is True
    assert "MISSING_CREDENTIAL" in (result.fatal_error or "")


def test_dsh_readiness_accepts_key_from_dsh_home_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from argus_skill.core.backend_readiness import _probe_cli_auth

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# comment\nDEEPSEEK_API_KEY=sk-from-env-file\n",
        encoding="utf-8",
    )

    ready, detail = _probe_cli_auth("dsh", "dsh", timeout_s=5.0)

    assert ready is True
    assert detail == ""


def test_dsh_readiness_rejects_without_any_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from argus_skill.core.backend_readiness import _probe_cli_auth

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DSH_HOME", str(tmp_path))

    ready, detail = _probe_cli_auth("dsh", "dsh", timeout_s=5.0)

    assert ready is False
    assert "DEEPSEEK_API_KEY" in detail


def test_dsh_finalize_does_not_touch_completed_state() -> None:
    """A turn that already completed through the normal path is left alone."""
    runner = _runner()
    state = _state(stdout_lines=["legacy"], stderr_lines=[])
    state.turn_completed = True
    state.agent_messages = ["legacy"]
    process = _FakeProcess(returncode=0, stdout_lines=[], stderr_lines=[])

    result = runner._finalize_turn_result(
        process=process,
        command=["dsh"],
        options=RunnerOptions(),
        state=state,
    )

    assert result.turn_completed is True
    assert result.agent_messages == ["legacy"]
