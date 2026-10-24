"""Regression coverage for localized Manager backend-result semantics."""

from __future__ import annotations

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli.models import AgentRunResult


def test_adapter_preserves_turn_failed_when_fatal_normalizes_away() -> None:
    backend = AgentCliBackend()
    translated = backend._translate_result(
        AgentRunResult(
            command=["codex"],
            exit_code=0,
            thread_id="thread-1",
            agent_messages=["Routed."],
            turn_completed=False,
            turn_failed=True,
            fatal_error="Reconnecting... 1/3",
        )
    )

    assert translated.exit_code == 0
    assert translated.fatal_error == "Agent CLI exited without completing a model turn."
