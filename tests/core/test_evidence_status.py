"""The shared four-state model, and the mis-kills it exists to prevent.

Each case here is a way an agent used to turn "the experiment did not run"
into "the idea is wrong".
"""
from __future__ import annotations

import pytest

from argus.core.evidence_status import (
    BASE_NON_IDEA_FAILURES,
    EvidenceContract,
    validate_evidence,
)

CONTRACT = EvidenceContract(
    domain="test",
    failure_classes=frozenset(
        {"none", "environment", "implementation", "empirical", "prior_art"}
    ),
    non_idea_failures=frozenset({"environment"}),
    grounding_fields=("baseline",),
    refuting_failures=frozenset({"empirical"}),
    advisory_failures=frozenset({"prior_art"}),
)


def record(**overrides):
    base = {
        "execution_status": "completed",
        "failure_class": "none",
        "idea_status": "inconclusive",
        "summary": "ran the probe",
        "evidence": "artifacts/run-1.json",
        "baseline": "main@abc123",
    }
    base.update(overrides)
    return base


def test_a_clean_record_passes() -> None:
    assert validate_evidence(record(), CONTRACT) == []


# -- the central invariant --------------------------------------------------

def test_environment_failure_cannot_refute_an_idea() -> None:
    errors = validate_evidence(
        record(execution_status="blocked", failure_class="environment", idea_status="refuted"),
        CONTRACT,
    )

    assert any("must be untested or inconclusive" in e for e in errors)


def test_environment_failure_cannot_support_an_idea_either() -> None:
    errors = validate_evidence(
        record(execution_status="blocked", failure_class="environment", idea_status="supported"),
        CONTRACT,
    )

    assert any("must be untested or inconclusive" in e for e in errors)


@pytest.mark.parametrize("idea", ["untested", "inconclusive"])
def test_environment_failure_permits_the_unresolved_states(idea) -> None:
    errors = validate_evidence(
        record(execution_status="blocked", failure_class="environment", idea_status=idea),
        CONTRACT,
    )

    assert errors == []


def test_an_attempt_that_did_not_complete_cannot_conclude_anything() -> None:
    errors = validate_evidence(
        record(execution_status="failed", failure_class="empirical", idea_status="refuted"),
        CONTRACT,
    )

    assert any("cannot support or refute" in e for e in errors)


# -- what may carry a refutation -------------------------------------------

def test_a_real_measurement_may_refute() -> None:
    errors = validate_evidence(
        record(failure_class="empirical", idea_status="refuted"), CONTRACT
    )

    assert errors == []


def test_an_inadequate_implementation_may_not_refute() -> None:
    # Under-performance is a statement about the code until proven otherwise.
    errors = validate_evidence(
        record(failure_class="implementation", idea_status="refuted"), CONTRACT
    )

    assert any("requires a completed valid result of class" in e for e in errors)


# -- scheduling signals are not evidence ------------------------------------

def test_prior_art_cannot_refute_an_idea() -> None:
    errors = validate_evidence(
        record(failure_class="prior_art", idea_status="refuted"), CONTRACT
    )

    assert any("scheduling/scope signal, not evidence" in e for e in errors)


def test_prior_art_is_fine_alongside_an_unresolved_idea() -> None:
    errors = validate_evidence(
        record(failure_class="prior_art", idea_status="inconclusive"), CONTRACT
    )

    assert errors == []


# -- grounding --------------------------------------------------------------

@pytest.mark.parametrize("idea", ["supported", "refuted"])
def test_a_conclusive_verdict_requires_its_grounding(idea) -> None:
    errors = validate_evidence(
        record(failure_class="empirical", idea_status=idea, baseline="REPLACE with baseline"),
        CONTRACT,
    )

    assert any("baseline is required" in e for e in errors)


def test_unresolved_verdicts_do_not_require_grounding() -> None:
    errors = validate_evidence(
        record(idea_status="untested", baseline="REPLACE with baseline"), CONTRACT
    )

    assert errors == []


# -- vocabulary and consistency --------------------------------------------

def test_unknown_values_are_rejected() -> None:
    errors = validate_evidence(
        record(execution_status="maybe", failure_class="vibes", idea_status="probably"),
        CONTRACT,
    )

    assert len(errors) >= 3


def test_no_failure_implies_a_completed_run() -> None:
    errors = validate_evidence(
        record(execution_status="blocked", failure_class="none", idea_status="untested"),
        CONTRACT,
    )

    assert any("failure_class=none requires" in e for e in errors)


def test_templated_prose_is_not_evidence() -> None:
    errors = validate_evidence(record(summary="REPLACE with the outcome"), CONTRACT)

    assert any("summary is empty or templated" in e for e in errors)


def test_errors_are_deduplicated_and_ordered() -> None:
    errors = validate_evidence(record(summary="", evidence=""), CONTRACT)

    assert errors == list(dict.fromkeys(errors))


# -- contract construction --------------------------------------------------

def test_contract_rejects_a_non_idea_failure_it_does_not_define() -> None:
    with pytest.raises(ValueError, match="non_idea_failures"):
        EvidenceContract(
            domain="broken",
            failure_classes=frozenset({"none"}),
            non_idea_failures=frozenset({"environment"}),
        )


def test_contract_rejects_a_refuting_failure_it_does_not_define() -> None:
    with pytest.raises(ValueError, match="refuting_failures"):
        EvidenceContract(
            domain="broken",
            failure_classes=frozenset({"none"}),
            non_idea_failures=frozenset(),
            refuting_failures=frozenset({"empirical"}),
        )


def test_base_non_idea_failures_are_a_subset_of_base_classes() -> None:
    from argus.core.evidence_status import BASE_FAILURE_CLASSES

    assert BASE_NON_IDEA_FAILURES <= BASE_FAILURE_CLASSES
