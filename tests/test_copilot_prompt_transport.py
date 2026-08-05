"""Regression guards for the schema-free Copilot prompt transport.

Reviewer and Planner model replies use named key/value footers. Large prompts
must still travel over stdin so they neither leak through argv nor hit E2BIG.
"""
from __future__ import annotations

from argus_skill.agent_cli.agent_cli_runner import (
    BACKEND_COPILOT,
    AgentCliRunner,
    RunnerOptions,
)


def _runner() -> AgentCliRunner:
    return AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)


def test_copilot_command_has_no_prompt_or_retired_schema_argv() -> None:
    command = _runner()._build_copilot_command(
        resume_thread_id=None,
        options=RunnerOptions(),
    )

    assert "-p" not in command
    assert "--prompt" not in command
    assert "--output-schema" not in command
    assert "--json-schema" not in command


def test_copilot_delivers_plain_named_protocol_prompt_on_stdin() -> None:
    command = _runner()._build_copilot_command(
        resume_thread_id=None,
        options=RunnerOptions(),
    )
    prompt = "Review normally.\nSTATUS=done|continue|blocked|replan_requested"

    prepared, stdin_prompt = _runner()._prepare_prompt_delivery(command, prompt)

    assert prepared == command
    assert stdin_prompt == prompt
    assert "OUTPUT CONTRACT (STRICT)" not in stdin_prompt
    assert "JSON Schema" not in stdin_prompt


def test_copilot_resume_still_receives_current_prompt_over_stdin() -> None:
    command = _runner()._build_copilot_command(
        resume_thread_id="tid-123",
        options=RunnerOptions(),
    )

    prepared, stdin_prompt = _runner()._prepare_prompt_delivery(command, "next round")

    assert prepared == command
    assert stdin_prompt == "next round"
