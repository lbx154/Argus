from __future__ import annotations

import pytest

from argus.roles.prompts import engineer


@pytest.mark.parametrize("include_static,compact_team", [(True, False), (False, False), (True, True)])
def test_long_task_rule_waits_in_turn_with_blocking_calls(
    monkeypatch: pytest.MonkeyPatch,
    include_static: bool,
    compact_team: bool,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")

    prompt = engineer.build_mission_prompt(
        task="Run a long evaluation.",
        skill_text="",
        next_action=None,
        include_static=include_static,
        compact_team=compact_team,
    )

    assert "setsid nohup" in prompt
    assert "<run>/exit_code" in prompt
    assert "a `yield_time_ms` longer than the job" in prompt
    assert "`async` mode, then end your turn" in prompt
    assert "Never poll in short loops" in prompt
    assert "keeps running after you hand off" in prompt
    assert "argus.tools.subagent" not in prompt
    assert "→ wait" not in prompt


@pytest.mark.parametrize("include_static,compact_team", [(True, False), (False, False), (True, True)])
def test_native_windows_rule_waits_with_wait_process(
    monkeypatch: pytest.MonkeyPatch,
    include_static: bool,
    compact_team: bool,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "native Windows")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "Win PS5.1")

    prompt = engineer.build_mission_prompt(
        task="Run a long evaluation.",
        skill_text="",
        next_action=None,
        include_static=include_static,
        compact_team=compact_team,
    )

    assert "Windows PowerShell 5.1 syntax" in prompt
    assert "Start-Process -PassThru" in prompt
    assert "`Wait-Process -Id <pid>`" in prompt
    assert "-Timeout" not in prompt
    assert "setsid" not in prompt
    assert "argus.tools.subagent" not in prompt
