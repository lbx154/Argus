from __future__ import annotations

import pytest

from argus_skill.roles.prompts import engineer


@pytest.mark.parametrize("include_static", [True, False])
def test_long_task_rule_requires_argus_durable_receipt(
    monkeypatch: pytest.MonkeyPatch,
    include_static: bool,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")

    prompt = engineer.build_mission_prompt(
        task="Run a long evaluation.",
        skill_text="",
        next_action=None,
        include_static=include_static,
    )

    assert '"${ARGUS_SKILL_PYTHON:-python3}" -m argus_skill.tools.subagent submit' in prompt
    assert "--mode direct" in prompt
    assert "--mode supervised" in prompt
    assert 'task(mode="background")' in prompt
    assert all(field in prompt for field in ("state=submitted", "task_id", "run_id", "check_with"))
    assert "state=discussing" in prompt
    assert "reply_with" in prompt
    assert "launch a supervised subagent" not in prompt


@pytest.mark.parametrize("include_static", [True, False])
def test_native_windows_rule_uses_powershell_durable_runner(
    monkeypatch: pytest.MonkeyPatch,
    include_static: bool,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "native Windows")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "Win PS5.1")

    prompt = engineer.build_mission_prompt(
        task="Run a long evaluation.",
        skill_text="",
        next_action=None,
        include_static=include_static,
    )

    assert "Native Windows preview cannot detach Argus subagents" not in prompt
    assert "Windows PowerShell 5.1 syntax" in prompt
    assert (
        "& '.\\.venv\\Scripts\\python.exe' -m argus_skill.tools.subagent submit"
        in prompt
    )
    assert "--mode direct" in prompt
    assert "--mode supervised" in prompt
    assert 'task(mode="background")' in prompt
    assert "session-owned background shell" in prompt
    assert all(field in prompt for field in ("state=submitted", "task_id", "run_id", "check_with"))
    assert "state=discussing" in prompt
    assert "reply_with" in prompt
    assert '"${ARGUS_SKILL_PYTHON:-python3}"' not in prompt
