from __future__ import annotations

import pytest

from argus.core.autonomy import (
    assess_operator_intervention,
    autonomous_operator_resolution,
    bounded_operator_wait_exit_seconds,
    normalize_autonomy_mode,
    normalize_operator_need,
    operator_available,
    operator_only_action,
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
            mode="pragmatic",
            unclassified_requires_operator=False,
        ).required is False
        assert assess_operator_intervention(
            question=question,
            mode="pragmatic",
            unclassified_requires_operator=True,
        ).required is True


def test_unclassified_questions_fail_safe_only_when_an_operator_exists(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    for mode in ("pragmatic", "autonomous"):
        decision = assess_operator_intervention(question="Ship it?", mode=mode)
        assert decision.required is True
        assert "no classification" in decision.reason
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    assert assess_operator_intervention(question="Ship it?", mode="pragmatic").required is False


def test_the_raising_roles_classification_decides(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "false")
    for need in ("credentials", "spending", "irreversible_or_external", "scope_or_authority"):
        decision = assess_operator_intervention(
            question="Which diagnostic should run next?",
            operator_need=need,
            mode="pragmatic",
        )
        assert decision.required is True
        assert decision.operator_need == need
    # The classification may also travel in the planner report.
    assert assess_operator_intervention(
        question="Ship it?",
        planner_report={"operator_need": "irreversible"},
        mode="autonomous",
    ).required is True
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    assert assess_operator_intervention(
        question="Ship it?", operator_need="none", mode="pragmatic"
    ).required is False


def test_autonomous_mode_still_sends_classified_action_needs_to_the_operator(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    for need in ("credentials", "spending", "irreversible_or_external"):
        assert assess_operator_intervention(
            question="Proceed?",
            planner_report={"authority_impact": "technical", "operator_need": need},
            mode="autonomous",
        ).required is True


def test_cautious_mode_asks_on_every_question() -> None:
    assert assess_operator_intervention(
        question="Which diagnostic should run next?",
        operator_need="none",
        mode="cautious",
    ).required is True


def test_invalid_mode_defaults_to_pragmatic() -> None:
    assert normalize_autonomy_mode("maximum-ish") == "pragmatic"


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


@pytest.mark.parametrize(
    ("alternative", "need"),
    [
        ("Force-push the protected release branch.", "irreversible_or_external"),
        ("Rewrite the published release history to drop the bad commit.", "irreversible_or_external"),
        ("Publish the package to PyPI.", "irreversible_or_external"),
        ("Deploy the service to production.", "irreversible_or_external"),
        ("Delete the stale shards outside the workspace.", "irreversible_or_external"),
        ("Purchase additional compute capacity.", "spending"),
        ("Use the production API key for the live check.", "credentials"),
        ("Use the operator's API credentials.", "credentials"),
    ],
)
def test_operator_only_action_backstop_names_the_action(alternative, need) -> None:
    assert operator_only_action(alternative) == need


@pytest.mark.parametrize(
    "alternative",
    [
        "Use the available local toolchain.",
        "Run the smaller benchmark row first.",
        "Do not force-push; rebase the local branch instead.",
        "Production credentials are unnecessary; use the local fixture.",
        "Use the published dataset already in the workspace.",
        "Overwrite the cached file in the workspace.",
        "",
    ],
)
def test_operator_only_action_backstop_stays_narrow(alternative) -> None:
    assert operator_only_action(alternative) == ""


def test_autonomous_resolution_blocks_only_on_operator_only_actions() -> None:
    assert autonomous_operator_resolution("credentials") == "blocked"
    assert autonomous_operator_resolution("spending") == "blocked"
    assert autonomous_operator_resolution("irreversible_or_external") == "blocked"
    assert autonomous_operator_resolution("scope_or_authority") == "assume"
    assert autonomous_operator_resolution("") == "assume"


@pytest.mark.parametrize("value", ["false", "0", "off", "no"])
def test_operator_availability_knob(value: str) -> None:
    assert operator_available(env={"ARGUS_SKILL_OPERATOR_AVAILABLE": value}) is False


def test_operator_is_available_by_default() -> None:
    assert operator_available(env={}) is True
    assert operator_available(env={"ARGUS_SKILL_OPERATOR_AVAILABLE": "true"}) is True


def test_bounded_operator_wait_grace() -> None:
    assert bounded_operator_wait_exit_seconds(
        env={"ARGUS_SKILL_OPERATOR_AVAILABLE": "false"}
    ) == 0.0
    assert bounded_operator_wait_exit_seconds(
        env={
            "ARGUS_SKILL_OPERATOR_AVAILABLE": "true",
            "ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN": "5",
        }
    ) == 300.0
    # 0 means wait indefinitely, for an operator who wants that.
    assert bounded_operator_wait_exit_seconds(
        env={
            "ARGUS_SKILL_OPERATOR_AVAILABLE": "true",
            "ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN": "0",
        }
    ) < 0
    assert bounded_operator_wait_exit_seconds(
        env={
            "ARGUS_SKILL_OPERATOR_AVAILABLE": "true",
            "ARGUS_SKILL_BOUNDED_OPERATOR_WAIT_EXIT_MIN": "garbage",
        }
    ) == 30 * 60.0


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
