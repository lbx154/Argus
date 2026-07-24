from __future__ import annotations

import json

import pytest

from argus_skill.core.models import ReviewDecision
from argus_skill.manager.stage_decider import (
    build_stage_decision_prompt,
    enforce_scientific_stage_guard,
    fallback_empty_stage_decision,
    final_stage_completion_decision,
    parse_stage_decision,
)

ORDER = ("research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission")


def _review(status: str = "done") -> ReviewDecision:
    return ReviewDecision(
        status=status,
        reason="Reviewer inspected the evidence and made this judgment.",
        next_action="" if status == "done" else "Continue the work.",
    )


def test_prompt_uses_minimal_reviewer_verdict() -> None:
    prompt = build_stage_decision_prompt(
        current_stage="research",
        next_stage="plan",
        earlier_stages=(),
        checklist_md="Read the actual evidence.",
        review=_review(),
    )

    assert "status: done" in prompt
    assert "Reviewer inspected the evidence" in prompt
    for removed in (
        "scientific_decision",
        "planner_report",
        "Harness arbitration",
        "Reviewer per-item checklist",
    ):
        assert removed not in prompt


def test_parse_advance_immediate_ok() -> None:
    decision = parse_stage_decision(
        '{"action":"advance","target_stage":"plan","reason":"ok"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "advance"
    assert decision.target_stage == "plan"


def test_parse_advance_cannot_skip_stage() -> None:
    decision = parse_stage_decision(
        '{"action":"advance","target_stage":"benchmark","reason":"skip"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "hold"
    assert decision.diagnostic == "illegal_advance_target"


def test_parse_rollback_requires_earlier_stage() -> None:
    valid = parse_stage_decision(
        '{"action":"rollback","target_stage":"plan","reason":"repair"}',
        current_stage="run",
        stage_order=ORDER,
    )
    invalid = parse_stage_decision(
        '{"action":"rollback","target_stage":"draft","reason":"bad"}',
        current_stage="run",
        stage_order=ORDER,
    )
    assert valid.action == "rollback"
    assert invalid.action == "hold"


@pytest.mark.parametrize("target", ["`plan`", "plan stage"])
def test_target_formatting_is_normalized(target: str) -> None:
    decision = parse_stage_decision(
        json.dumps({"action": "advance", "target_stage": target, "reason": "ok"}),
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "advance"
    assert decision.target_stage == "plan"


def test_invalid_manager_output_holds() -> None:
    decision = parse_stage_decision(
        '{"action":"unknown"}',
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "hold"


def test_empty_manager_output_always_holds() -> None:
    decision = fallback_empty_stage_decision(
        _review(),
        current_stage="research",
        stage_order=ORDER,
    )
    assert decision.action == "hold"
    assert decision.diagnostic == "empty_output_no_manager_judgment"


def test_final_submission_done_can_complete_final_stage() -> None:
    decision = final_stage_completion_decision(
        _review(),
        current_stage="submission",
        stage_order=ORDER,
        mission_scope="final_submission",
    )
    assert decision is not None
    assert decision.action == "complete"


def test_bounded_done_does_not_auto_complete_final_stage() -> None:
    decision = final_stage_completion_decision(
        _review(),
        current_stage="submission",
        stage_order=ORDER,
        mission_scope="bounded",
    )
    assert decision is None


def test_no_second_machine_value_guard_overrides_manager() -> None:
    manager = parse_stage_decision(
        '{"action":"advance","target_stage":"plan","reason":"review accepted"}',
        current_stage="research",
        stage_order=ORDER,
    )
    guarded = enforce_scientific_stage_guard(
        manager,
        _review(),
        current_stage="research",
    )
    assert guarded == manager
