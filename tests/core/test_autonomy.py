from __future__ import annotations

from argus.core.autonomy import (
    assess_operator_intervention,
    normalize_autonomy_mode,
    technical_continuation,
)


def test_pragmatic_mode_keeps_technical_choice_inside_argus() -> None:
    decision = assess_operator_intervention(
        question="Should the benchmark use a smaller diagnostic shape?",
        reason="The largest row timed out.",
        planner_report={"authority_impact": "technical"},
        mode="pragmatic",
    )

    assert decision.required is False
    assert "recoverable" in decision.reason


def test_operator_owned_acceptance_change_still_asks() -> None:
    decision = assess_operator_intervention(
        question="Is fp16 precision loss acceptable?",
        planner_report={"authority_impact": "operator"},
        mode="pragmatic",
    )

    assert decision.required is True


def test_legacy_question_detects_narrow_authority_boundary() -> None:
    assert assess_operator_intervention(
        question="May I force-push this release branch?",
        mode="pragmatic",
    ).required is True
    # A mistaken model label cannot waive an actual irreversible boundary.
    assert assess_operator_intervention(
        question="May I force-push this release branch?",
        planner_report={"authority_impact": "technical"},
        mode="pragmatic",
    ).required is True
    assert assess_operator_intervention(
        question="Should I inspect the timeout with one repeat?",
        mode="pragmatic",
    ).required is False


def test_cautious_mode_asks_but_autonomous_mode_keeps_technical_work() -> None:
    assert assess_operator_intervention(
        question="Which diagnostic should run next?",
        mode="cautious",
    ).required is True
    assert assess_operator_intervention(
        question="Which diagnostic should run next?",
        mode="autonomous",
    ).required is False
    assert assess_operator_intervention(
        question="Can I use the production API key?",
        mode="autonomous",
    ).required is True


def test_invalid_mode_defaults_to_pragmatic() -> None:
    assert normalize_autonomy_mode("maximum-ish") == "pragmatic"


def test_technical_continuation_prefers_reviewers_concrete_action() -> None:
    assert technical_continuation(
        question="What now?",
        next_action="Run the isolated one-row diagnostic.",
    ) == "Run the isolated one-row diagnostic."
    generated = technical_continuation(
        question="What now?",
        reason="the baseline timed out",
    )
    assert "smallest informative check" in generated
    assert "without waiting" in generated
