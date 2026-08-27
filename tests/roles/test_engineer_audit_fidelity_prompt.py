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


def test_performance_policy_uses_structured_work_kind_not_task_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engineer, "native_shell_contract", lambda: "")
    monkeypatch.setattr(engineer, "native_shell_summary", lambda: "")

    prose_only = engineer.build_mission_prompt(
        task="Benchmark and profile the GPU bottleneck.",
        skill_text="",
        next_action=None,
        work_kind="scope",
    )
    structured = engineer.build_mission_prompt(
        task="Investigate the assigned behavior.",
        skill_text="",
        next_action=None,
        work_kind="engineering_optimization",
    )

    assert "## Performance diagnosis" not in prose_only
    assert "## Performance diagnosis" in structured
