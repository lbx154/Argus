from __future__ import annotations

import pytest

from argus.roles.prompts import engineer
from tests.role_rules import lists, rule

_RULE = "long-runs-and-evidence/SKILL.md"


@pytest.mark.parametrize("compact_team", [False, True])
def test_the_prompt_points_at_the_long_run_rule(monkeypatch: pytest.MonkeyPatch, compact_team: bool) -> None:
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")
    prompt = engineer.build_mission_prompt(
        task="Run a long evaluation.", skill_text="", next_action=None, compact_team=compact_team,
    )
    assert lists(prompt, _RULE)
    assert "setsid" not in prompt


def test_long_task_rule_waits_in_turn_with_blocking_calls() -> None:
    text = rule("engineer", _RULE)
    assert "setsid nohup" in text
    assert "<run>/exit_code" in text
    assert "a `yield_time_ms` longer than the job" in text
    assert "`async` mode, then end your turn" in text
    assert "Never poll in short loops" in text
    assert "argus.tools.subagent" not in text


def test_native_windows_rule_waits_with_wait_process() -> None:
    text = rule("engineer", _RULE)
    assert "Start-Process -PassThru" in text
    assert "`Wait-Process -Id <pid>`" in text
    assert "-Timeout" not in text
