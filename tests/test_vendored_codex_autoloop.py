"""Sanity tests for the vendored ``agent_cli`` module.

argus drives the codex/claude/copilot/cursor/opencode/pi/grok CLI using nothing more
than its own wheel — the bundled ``argus.agent_cli`` package is the
only supported runtime. These tests fail loudly if the vendored copy ever
gets dropped or its public surface diverges from what
``argus.adapters.agent_cli_backend`` expects.
"""
from __future__ import annotations


def test_vendored_agent_cli_runner_importable() -> None:
    from argus.agent_cli.agent_cli_runner import (
        AgentCliRunner,
        RunnerOptions,
    )
    assert AgentCliRunner.__module__ == "argus.agent_cli.agent_cli_runner"
    assert RunnerOptions.__module__ == "argus.agent_cli.agent_cli_runner"


def test_agent_cli_package_init_is_thin() -> None:
    """The vendored package must not eagerly import the deleted legacy stack.

    Only the low-level CLI driver (agent_cli_runner/runner_backend/models)
    survives; importing the package must not pull in an orchestrator,
    telegram/feishu daemon, or a second reviewer/planner.
    """
    import argus.agent_cli as pkg

    assert pkg.__all__ == []
    for legacy in (
        "AutoLoopOrchestrator",
        "AutoLoopConfig",
        "LoopEngine",
        "LoopConfig",
    ):
        assert not hasattr(pkg, legacy), f"legacy symbol leaked: {legacy}"


def test_claude_command_has_no_retired_output_schema_flags() -> None:
    from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from argus.agent_cli.runner_backend import BACKEND_CLAUDE

    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)
    command = runner._build_command(
        resume_thread_id=None,
        options=RunnerOptions(),
    )

    assert "--json-schema" not in command
    assert "--output-schema" not in command
