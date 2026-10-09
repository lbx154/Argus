from __future__ import annotations

import pytest

from argus.core.autonomy import (
    assess_operator_intervention,
    autonomous_operator_resolution,
    bounded_operator_wait_exit_seconds,
    normalize_autonomy_mode,
    normalize_operator_need,
    operator_available,
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
    # The sentence that parked a headless run: it names credentials only to
    # say they are not needed. Without the raising role's classification the
    # question stays with the team.
    negated = assess_operator_intervention(
        question=(
            "Provide the Reviewer with original packet access and bounded local "
            "execution; production credentials are unnecessary."
        ),
        reason="Reviewer cannot read the packet.",
        mode="pragmatic",
    )
    assert negated.required is False
    for question in (
        "May I force-push this release branch?",
        "Can I use the production API key?",
        "Is the password policy acceptable?",
    ):
        assert assess_operator_intervention(
            question=question, mode="pragmatic"
        ).required is False


def test_the_raising_roles_classification_decides() -> None:
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
        planner_report={"operator_need": "irreversible_or_external"},
        mode="autonomous",
    ).required is True
    # An unknown label is not a classification.
    assert assess_operator_intervention(
        question="Which diagnostic should run next?",
        operator_need="maybe",
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
        question="Use the production deployment key?",
        operator_need="credentials",
        mode="autonomous",
    ).required is True


def test_invalid_mode_defaults_to_pragmatic() -> None:
    assert normalize_autonomy_mode("maximum-ish") == "pragmatic"


def test_operator_need_normalization() -> None:
    assert normalize_operator_need("Scope-Or-Authority") == "scope_or_authority"
    assert normalize_operator_need("none") == ""
    assert normalize_operator_need(None) == ""


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
