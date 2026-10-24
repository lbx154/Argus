"""The per-call provider-turn allowance (``ARGUS_SKILL_PROVIDER_TURN_CAP``).

One CLI call is one conversation with the provider, and the CLI resends the
whole grown transcript on every provider turn inside it — a measured worst case
was 430 provider turns and 59.9M input tokens in ONE Engineer call. None of the
driven CLIs accepts a per-call turn limit on its command line, so the runner
counts provider turns from the event stream and winds the call down at the
allowance; the round loop then continues the task in a fresh session.

These tests drive the real ``AgentCliRunner.run_exec`` streaming path with a
faked subprocess (no binary, no network, no spend).
"""

from __future__ import annotations

import pytest

from argus.agent_cli._env import _provider_turn_cap
from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.runner_backend import (
    BACKEND_CLAUDE,
    BACKEND_CODEX,
    BACKEND_COPILOT,
    BACKEND_OPENCODE,
    BACKEND_PI,
)


def test_wind_down_call_gets_only_a_small_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "40")
    assert _provider_turn_cap("engineer-r3.winddown") == 8
    monkeypatch.setenv("ARGUS_SKILL_PROVIDER_TURN_CAP", "4")
    assert _provider_turn_cap("engineer-r3.winddown") == 4


@pytest.mark.parametrize(
    ("backend", "event", "ends_turn"),
    [
        (BACKEND_COPILOT, {"type": "model.call_finished", "data": {}}, True),
        (
            BACKEND_COPILOT,
            {"type": "model.call_finished", "agentId": "native-agent", "data": {}},
            False,
        ),
        (
            BACKEND_COPILOT,
            {"type": "model.call_finished", "agentId": None, "data": {}},
            True,
        ),
        (
            BACKEND_COPILOT,
            {"type": "model.call_finished", "agentId": "", "data": {}},
            True,
        ),
        (
            BACKEND_COPILOT,
            {"type": "model.call_finished", "parentId": "previous-event", "data": {}},
            True,
        ),
        (BACKEND_COPILOT, {"type": "assistant.message_delta", "data": {}}, False),
        (BACKEND_CLAUDE, {"type": "assistant", "message": {}}, True),
        (BACKEND_CLAUDE, {"type": "assistant", "agentId": "agent", "message": {}}, True),
        (BACKEND_CLAUDE, {"type": "result", "subtype": "success"}, False),
        (
            BACKEND_CODEX,
            {"type": "item.completed", "item": {"type": "command_execution"}},
            True,
        ),
        (
            BACKEND_CODEX,
            {"type": "item.completed", "item": {"type": "reasoning"}},
            False,
        ),
        (BACKEND_OPENCODE, {"type": "step_finish", "part": {"reason": "tool-calls"}}, True),
        (BACKEND_OPENCODE, {"type": "text", "part": {"text": "hi"}}, False),
        (
            BACKEND_PI,
            {"type": "message_end", "message": {"role": "assistant"}},
            True,
        ),
        (
            BACKEND_PI,
            {"type": "message_end", "message": {"role": "user"}},
            False,
        ),
    ],
)
def test_provider_turn_boundaries_per_dialect(backend, event, ends_turn) -> None:
    runner = AgentCliRunner.__new__(AgentCliRunner)
    runner.backend = backend
    assert runner._event_ends_provider_turn(event) is ends_turn
