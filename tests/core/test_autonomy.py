from __future__ import annotations

import pytest

from argus.core.autonomy import (
    assess_operator_intervention,
    normalize_operator_need,
    operator_available,
    technical_continuation,
)


def test_technical_choice_stays_inside_argus() -> None:
    decision = assess_operator_intervention(
        question="Should the benchmark use a smaller diagnostic shape?",
        reason="The largest row timed out.",
        planner_report={"authority_impact": "technical"},
    )

    assert decision.required is False
    assert "recoverable" in decision.reason


def test_operator_owned_acceptance_change_still_asks() -> None:
    decision = assess_operator_intervention(
        question="Is fp16 precision loss acceptable?",
        planner_report={"authority_impact": "operator"},
    )

    assert decision.required is True


def test_words_in_the_question_never_decide_on_their_own() -> None:
    # The sentence that parked a headless run names credentials only to say
    # they are not needed. Words neither park nor release a question: the
    # outcome is the same as for any other unclassified question.
    for question in (
        "Provide the Reviewer with original packet access and bounded local "
        "execution; production credentials are unnecessary.",
        "May I force-push this release branch?",
        "Which diagnostic should run next?",
    ):
        assert assess_operator_intervention(
            question=question,
            unclassified_requires_operator=False,
        ).required is False
        assert assess_operator_intervention(
            question=question,
            unclassified_requires_operator=True,
        ).required is True


def test_unclassified_questions_stay_with_the_team_unless_explicitly_handed_off(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    decision = assess_operator_intervention(question="Ship it?")
    assert decision.required is False
    # Only the caller that saw an explicit hand-off asks for the fail-safe.
    assert assess_operator_intervention(
        question="Ship it?", unclassified_requires_operator=True
    ).required is True


def test_the_raising_roles_classification_decides(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    for need in ("credentials", "spending", "irreversible_or_external", "scope_or_authority"):
        decision = assess_operator_intervention(
            question="Which diagnostic should run next?",
            operator_need=need,
        )
        assert decision.required is True
        assert decision.operator_need == need
    # The classification may also travel in the planner report.
    assert assess_operator_intervention(
        question="Ship it?",
        planner_report={"operator_need": "irreversible"},
    ).required is True
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    assert assess_operator_intervention(
        question="Ship it?", operator_need="none"
    ).required is False


def test_classified_action_needs_go_to_the_operator(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    for need in ("credentials", "spending", "irreversible_or_external"):
        assert assess_operator_intervention(
            question="Proceed?",
            planner_report={"authority_impact": "technical", "operator_need": need},
        ).required is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("credentials", "credentials"),
        ("Credentials.", "credentials"),
        ("credentials (staging deploy key)", "credentials"),
        ("credentials|spending", "credentials"),
        ("spending|credentials", "spending"),
        ("irreversible", "irreversible_or_external"),
        ("external", "irreversible_or_external"),
        ("irreversible_or_external", "irreversible_or_external"),
        ("Scope-Or-Authority", "scope_or_authority"),
        ("authority", "scope_or_authority"),
        ("none", "none"),
        ("None.", "none"),
        ("technical", "none"),
        ("maybe", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_operator_need_normalization(value, expected) -> None:
    assert normalize_operator_need(value) == expected


@pytest.mark.parametrize("value", ["false", "0", "off", "no"])
def test_operator_availability_knob(value: str) -> None:
    assert operator_available(env={"ARGUS_SKILL_OPERATOR_AVAILABLE": value}) is False


def test_operator_is_available_by_default() -> None:
    assert operator_available(env={}) is True
    assert operator_available(env={"ARGUS_SKILL_OPERATOR_AVAILABLE": "true"}) is True


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
