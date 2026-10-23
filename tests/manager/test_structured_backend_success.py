"""Regression coverage for localized Manager backend-result semantics."""

from __future__ import annotations

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.agent_cli.models import AgentRunResult

_FAST_DECISION = {"choice": "existing", "vertical": "software", "workflow_mode": "direct"}
_CODEX_WARNING = "failed to record rollout items: thread example not found"


class _Result:
    def __init__(
        self,
        message: str = "Routed.",
        decision: dict | None = _FAST_DECISION,
        *,
        exit_code: int = 0,
        turn_completed: bool = True,
        turn_failed: bool = False,
        fatal_error: str | None = None,
        stderr_lines: list[str] | None = None,
        thread_id: str = "thread-1",
        tool_activity_observed: bool = True,
    ) -> None:
        self.exit_code = exit_code
        self.turn_completed = turn_completed
        self.turn_failed = turn_failed
        self.fatal_error = fatal_error
        self.stderr_lines = list(stderr_lines or [])
        self.agent_messages = [message] if message else []
        self.last_agent_message = message
        self.thread_id = thread_id
        self.tool_activity_observed = tool_activity_observed
        self.decision = decision










def test_adapter_preserves_turn_failed_for_manager_consumers() -> None:
    backend = AgentCliBackend()
    translated = backend._translate_result(
        AgentRunResult(
            command=["codex"],
            exit_code=0,
            thread_id="thread-1",
            agent_messages=["Routed."],
            turn_completed=False,
            turn_failed=True,
            fatal_error=None,
            stderr_lines=["structured turn failed without a fatal event"],
        )
    )

    assert translated.exit_code == 0
    assert translated.fatal_error == "structured turn failed without a fatal event"
    assert translated.stderr_lines == [
        "structured turn failed without a fatal event"
    ]


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








