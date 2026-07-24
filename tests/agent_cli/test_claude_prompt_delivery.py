from __future__ import annotations

import json

import pytest

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.runner_backend import BACKEND_CLAUDE, BACKEND_CODEX


def test_claude_prompt_is_positional() -> None:
    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)

    command, stdin_prompt = runner._prepare_prompt_delivery(
        ["claude", "-p", "--verbose"],
        "classify this message",
    )

    assert command == ["claude", "-p", "classify this message", "--verbose"]
    assert stdin_prompt is None


def test_non_claude_prompt_stays_on_stdin() -> None:
    runner = AgentCliRunner(agent_bin="codex", backend=BACKEND_CODEX)

    command, stdin_prompt = runner._prepare_prompt_delivery(
        ["codex", "exec", "-"],
        "implement the task",
    )

    assert command == ["codex", "exec", "-"]
    assert stdin_prompt == "implement the task"


def test_claude_bare_prompt_uses_stream_json_stdin() -> None:
    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)

    command, stdin_prompt = runner._prepare_prompt_delivery(
        ["claude", "-p", "--bare", "--verbose"],
        "classify this message",
    )

    assert command[-2:] == ["--input-format", "stream-json"]
    payload = json.loads(str(stdin_prompt))
    assert payload["message"]["content"] == "classify this message"


def test_claude_rejects_oversized_positional_prompt() -> None:
    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)

    with pytest.raises(RuntimeError, match="safe positional argument limit"):
        runner._prepare_prompt_delivery(
            ["claude", "-p", "--verbose"],
            "x" * 100_001,
        )
