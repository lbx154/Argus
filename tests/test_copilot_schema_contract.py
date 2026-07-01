"""Copilot has no --output-schema flag → the schema is embedded in the prompt.

EN: Codex/claude hard-enforce the reviewer/planner JSON schema via a CLI flag
(--output-schema / --json-schema); copilot (@github/copilot) has none, so
``_build_copilot_command`` must append the compact schema + a strict "reply with
ONLY JSON" instruction to the prompt — otherwise the reviewer verdict (the sole
done-authority) degrades to a non-JSON reply and the loop can't finish.
中文：codex/claude 用 CLI flag 硬约束 reviewer/planner 的 JSON schema；copilot
没有，故 ``_build_copilot_command`` 必须把压缩 schema + "只回 JSON" 指令追加进
prompt，否则 reviewer 裁决退化成非 JSON、loop 跑不完。
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


def _prompt_arg(command: list[str]) -> str:
    # copilot passes the prompt via `-p <text>` (last element pair).
    return command[command.index("-p") + 1]


def _runner() -> AgentCliRunner:
    return AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT)


def test_copilot_embeds_schema_in_prompt(tmp_path):
    cmd = _runner()._build_copilot_command(
        prompt="do the thing",
        resume_thread_id=None,
        options=RunnerOptions(output_schema_path=_schema_file(tmp_path)),
    )
    # copilot has no schema flag...
    assert "--output-schema" not in cmd
    assert "--json-schema" not in cmd
    # ...so the schema + strict JSON instruction is embedded in the prompt.
    prompt = _prompt_arg(cmd)
    assert prompt.startswith("do the thing")
    assert "OUTPUT CONTRACT (STRICT)" in prompt
    assert '"required":["status"]' in prompt  # compact schema present


def test_copilot_no_suffix_when_schema_absent(tmp_path):
    cmd = _runner()._build_copilot_command(
        prompt="p", resume_thread_id=None, options=RunnerOptions()
    )
    assert _prompt_arg(cmd) == "p"  # no output_schema_path → prompt untouched


def test_copilot_no_suffix_on_resume(tmp_path):
    # On a resumed thread the contract already lives in the conversation.
    cmd = _runner()._build_copilot_command(
        prompt="p",
        resume_thread_id="tid-123",
        options=RunnerOptions(output_schema_path=_schema_file(tmp_path)),
    )
    assert _prompt_arg(cmd) == "p"
    assert "OUTPUT CONTRACT" not in _prompt_arg(cmd)


def test_copilot_schema_suffix_fail_soft(tmp_path):
    # A missing/invalid schema path must NOT block the run (fail-open to "").
    cmd = _runner()._build_copilot_command(
        prompt="p",
        resume_thread_id=None,
        options=RunnerOptions(output_schema_path=str(tmp_path / "nope.json")),
    )
    assert _prompt_arg(cmd) == "p"  # no crash, no suffix
