from __future__ import annotations

import json

import pytest

from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.runner_backend import BACKEND_CLAUDE


def test_claude_bare_prompt_uses_stream_json_stdin() -> None:
    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)

    command, stdin_prompt, cleanup_path = runner._prepare_prompt_delivery(
        ["claude", "-p", "--bare", "--verbose"],
        "classify this message",
    )

    assert command[-2:] == ["--input-format", "stream-json"]
    payload = json.loads(str(stdin_prompt))
    assert payload["message"]["content"] == "classify this message"
    assert cleanup_path is None


def test_windows_claude_wrapper_receives_prompt_on_stdin() -> None:
    runner = AgentCliRunner(agent_bin="claude.cmd", backend=BACKEND_CLAUDE)

    command, stdin_prompt, cleanup_path = runner._prepare_prompt_delivery(
        [r"C:\Users\operator\AppData\Roaming\npm\claude.cmd", "-p", "--verbose"],
        "classify this message",
    )

    assert command == [
        r"C:\Users\operator\AppData\Roaming\npm\claude.cmd",
        "-p",
        "--verbose",
    ]
    assert stdin_prompt == "classify this message"
    assert cleanup_path is None


def test_claude_rejects_oversized_positional_prompt() -> None:
    runner = AgentCliRunner(agent_bin="claude", backend=BACKEND_CLAUDE)

    with pytest.raises(RuntimeError, match="safe positional argument limit"):
        runner._prepare_prompt_delivery(
            ["claude", "-p", "--verbose"],
            "x" * 100_001,
        )
