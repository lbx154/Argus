from __future__ import annotations

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
)


def test_claude_command_receives_dollar_fence() -> None:
    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)
    command = runner._build_claude_command(
        resume_thread_id=None,
        options=RunnerOptions(max_budget_usd=0.125),
    )
    index = command.index("--max-budget-usd")
    assert command[index + 1] == "0.125"


def test_copilot_command_receives_soft_ai_credit_fence() -> None:
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)
    command = runner._build_copilot_command(
        prompt="bounded",
        resume_thread_id=None,
        options=RunnerOptions(max_ai_credits=30),
    )
    index = command.index("--max-ai-credits")
    assert command[index + 1] == "30"


def test_manager_read_only_policy_is_backend_specific() -> None:
    codex = AgentCliRunner(
        agent_bin="codex",
        backend=BACKEND_CODEX,
        default_extra_args=["--dangerously-bypass-approvals-and-sandbox"],
    )
    codex_command = codex._build_codex_command(
        resume_thread_id="thread-1",
        options=RunnerOptions(sandbox_mode="read-only", working_dir="/workspace"),
    )
    assert "-s" not in codex_command
    assert "-C" not in codex_command
    assert 'sandbox_mode="read-only"' in codex_command
    assert "--dangerously-bypass-approvals-and-sandbox" not in codex_command

    claude = AgentCliRunner(
        agent_bin="claude",
        backend=BACKEND_CLAUDE,
        default_extra_args=[
            "--allowedTools", "Edit,Bash",
            "-c", "--tools=default",
        ],
    )
    claude_command = claude._build_claude_command(
        resume_thread_id=None,
        options=RunnerOptions(sandbox_mode="read-only"),
    )
    assert claude_command[claude_command.index("--tools") + 1] == "Read,Glob,Grep"
    assert "--permission-mode" not in claude_command
    assert "--allowedTools" not in claude_command
    assert "--tools=default" not in claude_command

    copilot = AgentCliRunner(
        agent_bin="copilot",
        backend=BACKEND_COPILOT,
        default_extra_args=["--allow-all-tools", "--available-tools", "write"],
    )
    copilot_command = copilot._build_copilot_command(
        prompt="inspect",
        resume_thread_id=None,
        options=RunnerOptions(sandbox_mode="read-only"),
    )
    assert copilot_command[
        copilot_command.index("--available-tools") + 1
    ] == "view,rg,glob"
    assert "--allow-all-tools" not in copilot_command
    assert "--yolo" not in copilot_command
    assert "write" not in copilot_command


def test_read_only_manager_never_uses_copilot_acp(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_ACP_LABELS", "manager-stage")
    runner = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)

    assert runner._acp_enabled(
        "manager-stage",
        RunnerOptions(sandbox_mode="read-only"),
    ) is False
    assert runner._acp_enabled("manager-stage", RunnerOptions()) is True


def test_read_only_filter_rejects_attached_codex_overrides() -> None:
    runner = AgentCliRunner(
        agent_bin="codex",
        backend=BACKEND_CODEX,
        default_extra_args=[
            '-c=sandbox_mode="danger-full-access"',
            '-csandbox_permissions=["disk-full-read-access"]',
            "-sdanger-full-access",
        ],
    )
    command = runner._build_codex_command(
        resume_thread_id="thread-1",
        options=RunnerOptions(sandbox_mode="read-only"),
    )

    rendered = " ".join(command)
    assert "danger-full-access" not in rendered
    assert "disk-full-read-access" not in rendered
