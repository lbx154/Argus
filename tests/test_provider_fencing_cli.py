from __future__ import annotations

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus_skill.agent_cli.runner_backend import BACKEND_CLAUDE, BACKEND_COPILOT


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
