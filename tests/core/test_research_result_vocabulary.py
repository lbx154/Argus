"""A contract keyed on five enums must say what the five enums accept.

Testbed run 15 (``s-f0dbba19``) solved its problem — a reproducible survey, a
both-directions proof, and a Lean 4 file building against Mathlib with no
``sorry`` and no ``axiom`` — and then could not close at its final stage. The
Manager's completion was refused with ``missing_or_invalid_research_result``
against Reviewer messages that visibly contained a research result.

Six Reviewer rounds emitted a ``RESEARCH_RESULT`` block. The contract accepted
none of them::

    result_class=proof            correctness_status=proved
    novelty_status=unknown        significance_status=bounded_complete
    statement_fidelity_status=supports

Nothing about those is careless. They are what a competent reader writes when
asked for ``significance_status`` and told nothing further, and the prompt told
it nothing further: it named the five fields and never once listed a legal
value. ``normalize_research_result`` then returned ``None`` for the whole
payload, discarding the evidence and limitations along with it.

The fix is that the prompt renders ``RESULT_FIELD_CHOICES`` — the same table
the validator reads — so the two cannot drift apart, and the refusal names the
field and the value it rejected instead of the whole block.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from argus_skill.core.research_contract import (
    RESULT_FIELD_CHOICES,
    normalize_research_result,
    research_completion_issue,
    research_result_rejection,
)

#: Verbatim from run 15's ``agent_io.jsonl``, one per Reviewer round, minus the
#: evidence and limitations arrays. Every one of them was thrown away.
RUN_15_EMISSIONS = (
    {
        "result_class": "proof",
        "correctness_status": "proved",
        "novelty_status": "unknown",
        "significance_status": "bounded_complete",
        "statement_fidelity_status": "supports",
    },
    {
        "result_class": "proof_and_formalization_review",
        "correctness_status": "certified",
        "novelty_status": "not_claimed",
        "significance_status": "satisfies_operator_objective",
        "statement_fidelity_status": "verified",
    },
    {
        "result_class": "proof",
        "correctness_status": "proved",
        "novelty_status": "unknown",
        "significance_status": "complete_for_requested_problem",
        "statement_fidelity_status": "supported",
    },
    {
        "result_class": "proof",
        "correctness_status": "proved",
        "novelty_status": "unknown",
        "significance_status": "complete_for_objective",
        "statement_fidelity_status": "faithful",
    },
    {
        "result_class": "proof",
        "correctness_status": "proved",
        "novelty_status": "not_applicable",
        "significance_status": "meets_objective",
        "statement_fidelity_status": "faithful",
    },
    {
        "result_class": "proof",
        "correctness_status": "proved",
        "novelty_status": "unknown",
        "significance_status": "complete_for_requested_scope",
        "statement_fidelity_status": "supported",
    },
)

VALID = {
    "result_class": "finite_verification",
    "correctness_status": "verified",
    "novelty_status": "known",
    "significance_status": "exploratory",
    "statement_fidelity_status": "verified",
    "evidence": ["Lean build exits 0 with no sorry and no axiom"],
    "limitations": [],
}


def _research_target_prompt(level: str = "publishable") -> str:
    from argus_skill.reviewer import Reviewer
    from argus_skill.skills.vertical_select import persist_vertical

    root = pathlib.Path(tempfile.mkdtemp())
    persist_vertical(root, "research", research_target_level=level)
    reviewer = Reviewer(runner=None, skill_store=None)
    return reviewer._build_prompt(
        objective="settle the conjecture",
        original_objective="settle the conjecture",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="done",
        main_error=None,
        prior_checkpoint={},
        working_dir=str(root),
        scope="bounded",
    )


@pytest.mark.parametrize("field, choices", RESULT_FIELD_CHOICES)
def test_the_prompt_lists_every_legal_value_of_every_gated_field(
    field: str, choices: tuple[str, ...]
) -> None:
    """What the validator enforces, the Reviewer is told.

    A field whose values only exist in ``research_contract`` is a field the
    model has to guess, and run 15 shows it guesses fluently and wrongly.
    """
    text = _research_target_prompt()

    assert field in text
    for value in choices:
        assert value in text, (
            f"{field} accepts {value!r} and the Reviewer prompt never says so"
        )


def test_the_prompt_does_not_invent_values_the_contract_rejects() -> None:
    """The other direction: no vocabulary in the prompt that would be refused.

    Rendering from ``RESULT_FIELD_CHOICES`` is what makes this hold; the test
    is here so a hand-written replacement cannot quietly reintroduce drift.
    """
    text = _research_target_prompt()

    for field, choices in RESULT_FIELD_CHOICES:
        line = next(
            ln for ln in text.splitlines() if ln.startswith(f"{field}: ")
        )
        assert line.split(": ", 1)[1].split() == list(choices)


@pytest.mark.parametrize("payload", RUN_15_EMISSIONS)
def test_run_15s_emissions_are_still_refused(payload: dict) -> None:
    """The vocabulary is not widened. ``proof`` is still not a result class.

    Fixing this by accepting whatever the model sent would make the enums
    decorative, and the completion gate reads them.
    """
    assert normalize_research_result(payload) is None


@pytest.mark.parametrize("payload", RUN_15_EMISSIONS)
def test_the_refusal_names_the_field_and_the_value(payload: dict) -> None:
    """An operator reading the trace can see what to change."""
    issue = research_result_rejection(payload)

    assert issue.startswith("invalid_research_result_fields:")
    for field, choices in RESULT_FIELD_CHOICES:
        if payload[field] not in choices:
            assert f"{field}={payload[field]}" in issue


def test_the_refusal_reaches_the_completion_gate() -> None:
    """The named detail is what the Manager's rejection now carries.

    Run 15's operator got ``missing_or_invalid_research_result`` and no way to
    tell which of five fields, out of a payload that was four-fifths right.
    """
    issue = research_completion_issue(
        RUN_15_EMISSIONS[-1], research_target_level="publishable"
    )

    assert "significance_status=complete_for_requested_scope" in issue
    assert "statement_fidelity_status=supported" in issue
    assert "correctness_status=proved" in issue


def test_a_genuinely_absent_result_still_says_so() -> None:
    """Nothing to name is a different failure from naming the wrong thing."""
    assert research_result_rejection(None) == "missing_or_invalid_research_result"
    assert research_result_rejection("RESEARCH_RESULT=") == (
        "missing_or_invalid_research_result"
    )


def test_an_empty_field_is_reported_as_empty_not_omitted() -> None:
    """``significance_status=`` reads like a truncation; say ``(empty)``."""
    payload = dict(VALID, significance_status="")

    assert "significance_status=(empty)" in research_result_rejection(payload)


def test_reviewed_new_candidate_can_complete_direct_idea_request() -> None:
    candidate = {
        "result_class": "new_candidate",
        "correctness_status": "verified",
        "novelty_status": "verified_new",
        "significance_status": "publishable",
        "statement_fidelity_status": "verified",
        "evidence": ["primary-source novelty review"],
        "limitations": [],
    }

    assert research_completion_issue(
        candidate,
        research_target_level="publishable",
        scope="idea_only",
    ) == ""
    assert (
        research_completion_issue(
            candidate,
            research_target_level="publishable",
        )
        == "result_class_below_publishable:new_candidate"
    )


def test_publishable_idea_requires_verified_novelty() -> None:
    candidate = {
        "result_class": "new_candidate",
        "correctness_status": "verified",
        "novelty_status": "unverified",
        "significance_status": "publishable",
        "statement_fidelity_status": "verified",
        "evidence": ["candidate proposal"],
        "limitations": [],
    }

    assert research_completion_issue(
        candidate,
        research_target_level="publishable",
        scope="idea_only",
    ) == "novelty_not_verified_new"


def test_exploratory_new_candidate_also_requires_verified_novelty() -> None:
    candidate = {
        "result_class": "new_candidate",
        "correctness_status": "verified",
        "novelty_status": "known",
        "significance_status": "exploratory",
        "statement_fidelity_status": "verified",
        "evidence": ["closest prior work"],
        "limitations": [],
    }

    assert research_completion_issue(
        candidate,
        research_target_level="exploratory",
        scope="idea_only",
    ) == "novelty_not_verified_new"


def test_a_legal_result_has_nothing_to_report() -> None:
    assert research_result_rejection(VALID) == ""
    assert normalize_research_result(VALID) is not None


def test_the_legacy_field_names_are_read_the_same_way_by_both() -> None:
    """The diagnostic must not refuse a payload the validator accepts.

    ``correctness``/``novelty``/``statement_fidelity`` are historical spellings
    that ``normalize_research_result`` still honours. A diagnostic that
    re-derived the fields could disagree with the check it explains.
    """
    legacy = {
        "result_class": "finite_verification",
        "correctness": "verified",
        "novelty": "known",
        "statement_fidelity": "verified",
        "evidence": ["checked by hand"],
    }

    assert normalize_research_result(legacy) is not None
    assert research_result_rejection(legacy) == ""


def test_every_rendered_value_survives_a_round_trip() -> None:
    """Each listed value is one a Reviewer can actually use.

    Substituting any single choice into an otherwise-valid payload must leave
    it readable, or the prompt is advertising a value the validator rejects.
    """
    for field, choices in RESULT_FIELD_CHOICES:
        for value in choices:
            payload = dict(VALID, **{field: value})
            assert normalize_research_result(payload) is not None, (
                f"{field}={value} is listed in the prompt and rejected by the "
                "validator"
            )
