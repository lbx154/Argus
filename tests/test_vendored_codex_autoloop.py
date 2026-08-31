"""Sanity tests for the vendored ``agent_cli`` module.

argus-skill drives the codex/claude/copilot/cursor/opencode/pi/grok CLI using nothing more
than its own wheel — the bundled ``argus_skill.agent_cli`` package is the
only supported runtime. These tests fail loudly if the vendored copy ever
gets dropped or its public surface diverges from what
``argus_skill.adapters.agent_cli_backend`` expects.
"""
from __future__ import annotations


def test_vendored_agent_cli_runner_importable() -> None:
    from argus_skill.agent_cli.agent_cli_runner import (
        AgentCliRunner,
        RunnerOptions,
    )
    assert AgentCliRunner.__module__ == "argus_skill.agent_cli.agent_cli_runner"
    assert RunnerOptions.__module__ == "argus_skill.agent_cli.agent_cli_runner"


def test_vendored_runner_backend_constants() -> None:
    from argus_skill.agent_cli.runner_backend import (
        BACKEND_CLAUDE,
        BACKEND_CODEX,
        BACKEND_COPILOT,
        BACKEND_CURSOR,
        BACKEND_GROK,
        BACKEND_OPENCODE,
        BACKEND_PI,
        DEFAULT_RUNNER_BACKEND,
        normalize_runner_backend,
    )
    assert BACKEND_CODEX == "codex"
    assert BACKEND_CLAUDE == "claude"
    assert BACKEND_COPILOT == "copilot"
    assert BACKEND_CURSOR == "cursor"
    assert BACKEND_OPENCODE == "opencode"
    assert BACKEND_PI == "pi"
    assert BACKEND_GROK == "grok"
    assert DEFAULT_RUNNER_BACKEND in {
        BACKEND_CODEX,
        BACKEND_CLAUDE,
        BACKEND_COPILOT,
        BACKEND_CURSOR,
        BACKEND_OPENCODE,
        BACKEND_PI,
        BACKEND_GROK,
    }
    assert normalize_runner_backend("CODEX") == BACKEND_CODEX
    assert normalize_runner_backend("copilot") == BACKEND_COPILOT
    assert normalize_runner_backend("opencod") == BACKEND_OPENCODE
    assert normalize_runner_backend("PI") == BACKEND_PI
    assert normalize_runner_backend("GROK") == BACKEND_GROK


def test_agent_cli_backend_resolver_uses_vendored_module() -> None:
    from argus_skill.adapters.agent_cli_backend._runtime import (
        load_agent_cli_runtime,
    )
    deps = load_agent_cli_runtime()
    runner_cls = deps["AgentCliRunner"]
    # The resolver only ever imports the bundled copy that ships with us.
    assert runner_cls.__module__.startswith("argus_skill.agent_cli"), (
        f"expected vendored agent_cli_runner; got {runner_cls.__module__}"
    )
    for required in (
        "AgentCliRunner",
        "CliRunnerOptions",
        "BACKEND_CLAUDE",
        "BACKEND_CODEX",
        "BACKEND_COPILOT",
        "BACKEND_CURSOR",
        "BACKEND_OPENCODE",
        "BACKEND_PI",
        "DEFAULT_RUNNER_BACKEND",
        "default_runner_bin",
        "normalize_runner_backend",
    ):
        assert required in deps, f"resolver missing {required}"


def test_agent_cli_package_init_is_thin() -> None:
    """The vendored package must not eagerly import the deleted legacy stack.

    Only the low-level CLI driver (agent_cli_runner/runner_backend/models)
    survives; importing the package must not pull in an orchestrator,
    telegram/feishu daemon, or a second reviewer/planner.
    """
    import argus_skill.agent_cli as pkg

    assert pkg.__all__ == []
    for legacy in (
        "AutoLoopOrchestrator",
        "AutoLoopConfig",
        "LoopEngine",
        "LoopConfig",
    ):
        assert not hasattr(pkg, legacy), f"legacy symbol leaked: {legacy}"


def test_claude_command_has_no_retired_output_schema_flags() -> None:
    from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
    from argus_skill.agent_cli.runner_backend import BACKEND_CLAUDE

    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)
    command = runner._build_command(
        resume_thread_id=None,
        options=RunnerOptions(),
    )

    assert "--json-schema" not in command
    assert "--output-schema" not in command
