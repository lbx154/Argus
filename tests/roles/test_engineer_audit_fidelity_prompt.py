from __future__ import annotations

import pytest

from argus_skill.roles.prompts import engineer


@pytest.mark.parametrize("include_static", [True, False])
def test_audit_words_do_not_inject_a_policy_block(
    monkeypatch: pytest.MonkeyPatch,
    include_static: bool,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")

    prompt = engineer.build_mission_prompt(
        task=(
            "Generate a paper and maintain an append-only issue ledger, "
            "COMMAND_LOG, process trace, and provenance audit."
        ),
        skill_text="",
        next_action=None,
        include_static=include_static,
    )

    assert "## Audit fidelity" not in prompt


def test_ordinary_task_also_has_no_audit_policy_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")

    prompt = engineer.build_mission_prompt(
        task="Implement the parser fix and run its unit test.",
        skill_text="",
        next_action=None,
    )

    assert "## Audit fidelity" not in prompt


def test_compact_team_performance_policy_states_when_it_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")

    prompt = engineer.build_mission_prompt(
        task="Investigate the assigned behavior.",
        skill_text="",
        next_action=None,
        compact_team=True,
    )

    assert "Performance root-cause/bottleneck/replacement claims need" in prompt
    assert "hot-path/live-resource evidence plus timing/profiling or controlled A/B" in prompt
