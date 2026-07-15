"""Copilot has no --output-schema flag → the schema is embedded in the prompt.

EN: Codex/claude hard-enforce the reviewer/planner JSON schema via a CLI flag
(--output-schema / --json-schema); copilot (@github/copilot) has none, so the
compact schema + a strict "reply with ONLY JSON" instruction is appended to the
prompt itself (``_effective_prompt``) — otherwise the reviewer verdict (the sole
done-authority) degrades to a non-JSON reply and the loop can't finish. The
prompt is delivered on STDIN (copilot reads stdin when no ``-p`` argv is given);
passing it via argv would trip the kernel per-arg limit and crash the reviewer
with OSError: [Errno 7] Argument list too long.
中文：codex/claude 用 CLI flag 硬约束 reviewer/planner 的 JSON schema；copilot
没有，故把压缩 schema + "只回 JSON" 指令追加进 prompt（``_effective_prompt``）并经
stdin 递交（不传 ``-p``，避免大 prompt 触发 E2BIG 崩溃 reviewer）。
"""
from __future__ import annotations

import json

from argus_skill.agent_cli.agent_cli_runner import (
    BACKEND_COPILOT,
    AgentCliRunner,
    RunnerOptions,
)


def _schema_file(tmp_path) -> str:
    p = tmp_path / "schema.json"
    p.write_text(
        json.dumps({"type": "object", "required": ["status"]}), encoding="utf-8"
    )
    return str(p)


def _runner() -> AgentCliRunner:
    return AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)


def test_copilot_command_never_passes_prompt_via_argv(tmp_path):
    # The prompt is delivered on stdin, never as an argv token: a large prompt
    # in argv trips E2BIG (Errno 7) and crashes the reviewer every round.
    cmd = _runner()._build_copilot_command(
        resume_thread_id=None,
        options=RunnerOptions(output_schema_path=_schema_file(tmp_path)),
    )
    assert "-p" not in cmd
    assert "--prompt" not in cmd
    # copilot has no schema flag either.
    assert "--output-schema" not in cmd
    assert "--json-schema" not in cmd


def test_copilot_embeds_schema_in_prompt(tmp_path):
    prompt = _runner()._effective_prompt(
        prompt="do the thing",
        resume_thread_id=None,
        options=RunnerOptions(output_schema_path=_schema_file(tmp_path)),
    )
    assert prompt.startswith("do the thing")
    assert "OUTPUT CONTRACT (STRICT)" in prompt
    assert '"required":["status"]' in prompt  # compact schema present


def test_copilot_no_suffix_when_schema_absent(tmp_path):
    prompt = _runner()._effective_prompt(
        prompt="p", resume_thread_id=None, options=RunnerOptions()
    )
    assert prompt == "p"  # no output_schema_path → prompt untouched


def test_copilot_no_suffix_on_resume(tmp_path):
    # On a resumed thread the contract already lives in the conversation.
    prompt = _runner()._effective_prompt(
        prompt="p",
        resume_thread_id="tid-123",
        options=RunnerOptions(output_schema_path=_schema_file(tmp_path)),
    )
    assert prompt == "p"
    assert "OUTPUT CONTRACT" not in prompt


def test_copilot_schema_suffix_fail_soft(tmp_path):
    # A missing/invalid schema path must NOT block the run (fail-open to "").
    prompt = _runner()._effective_prompt(
        prompt="p",
        resume_thread_id=None,
        options=RunnerOptions(output_schema_path=str(tmp_path / "nope.json")),
    )
    assert prompt == "p"  # no crash, no suffix
