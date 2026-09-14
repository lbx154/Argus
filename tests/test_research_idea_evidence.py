"""Research idea evidence: the three mis-kills specific to research work.

An under-performing pilot, an under-powered pilot, and an idea someone else
already published all produce a disappointing run. None of them is a refuted
hypothesis, and treating them as one is how promising work gets abandoned.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.verticals.research import idea_evidence as mod


def record(**overrides):
    base = {
        "schema_version": mod.SCHEMA_VERSION,
        "idea_id": "idea-007",
        "premise_version": 1,
        "premise": "sparse routing beats dense at equal FLOPs on task X",
        "execution_status": "completed",
        "failure_class": "empirical",
        "idea_status": "inconclusive",
        "evaluator_identity": "exact-match scorer @ eval/scorer.py:a1b2c3",
        "comparison_identity": "dense baseline @ run-2026-08-01",
        "summary": "sparse matched dense within noise",
        "evidence": "experiments/run-14/metrics.json",
    }
    base.update(overrides)
    return base


def test_a_clean_record_passes() -> None:
    assert mod.validate_idea_evidence(record()) == []


# -- mis-kill 1: an inadequate implementation is not a disproof -------------

def test_implementation_failure_cannot_refute_the_idea() -> None:
    errors = mod.validate_idea_evidence(
        record(failure_class="implementation", idea_status="refuted")
    )

    assert any("requires a completed valid result of class" in e for e in errors)


def test_a_real_measurement_can_refute_the_idea() -> None:
    assert mod.validate_idea_evidence(
        record(failure_class="empirical", idea_status="refuted")
    ) == []


# -- mis-kill 2: an under-powered pilot is inconclusive ---------------------

@pytest.mark.parametrize("idea", ["supported", "refuted"])
def test_low_statistical_power_cannot_settle_the_premise(idea) -> None:
    # N=1 / single-seed / noise-dominated: informative for the next probe,
    # not a verdict.
    errors = mod.validate_idea_evidence(
        record(failure_class="statistical_power", idea_status=idea)
    )

    assert any("must be untested or inconclusive" in e for e in errors)


def test_low_statistical_power_is_fine_as_inconclusive() -> None:
    assert mod.validate_idea_evidence(
        record(failure_class="statistical_power", idea_status="inconclusive")
    ) == []


# -- mis-kill 3: prior art is a replanning signal --------------------------

def test_prior_art_cannot_refute_the_idea() -> None:
    errors = mod.validate_idea_evidence(
        record(failure_class="prior_art", idea_status="refuted")
    )

    assert any("scheduling/scope signal, not evidence" in e for e in errors)


def test_scope_change_cannot_refute_the_idea() -> None:
    errors = mod.validate_idea_evidence(
        record(failure_class="scope_change", idea_status="refuted")
    )

    assert any("scheduling/scope signal, not evidence" in e for e in errors)


# -- environment and evaluator failures ------------------------------------

@pytest.mark.parametrize(
    "failure", ["environment", "dependency", "data_access", "evaluator_infrastructure"]
)
def test_infrastructure_failures_leave_the_idea_unresolved(failure) -> None:
    errors = mod.validate_idea_evidence(
        record(execution_status="blocked", failure_class=failure, idea_status="refuted")
    )

    assert any("must be untested or inconclusive" in e for e in errors)


def test_a_stubbed_evaluator_cannot_support_the_idea() -> None:
    # evaluator_infrastructure covers "the scorer returned a constant".
    errors = mod.validate_idea_evidence(
        record(failure_class="evaluator_infrastructure", idea_status="supported")
    )

    assert any("must be untested or inconclusive" in e for e in errors)


# -- premise versioning -----------------------------------------------------

def test_premise_is_required_even_when_untested() -> None:
    errors = mod.validate_idea_evidence(
        record(
            execution_status="blocked",
            failure_class="environment",
            idea_status="untested",
            premise="REPLACE with the exact falsifiable premise under test",
        )
    )

    assert any("premise is empty or templated" in e for e in errors)


def test_premise_version_must_be_an_integer() -> None:
    errors = mod.validate_idea_evidence(record(premise_version="v2"))

    assert any("premise_version must be an integer" in e for e in errors)


# -- grounding --------------------------------------------------------------

@pytest.mark.parametrize(
    "field", ["premise", "evaluator_identity", "comparison_identity"]
)
def test_a_verdict_requires_its_grounding(field) -> None:
    errors = mod.validate_idea_evidence(
        record(idea_status="supported", **{field: "REPLACE with the value"})
    )

    assert any(field in e for e in errors)


# -- template and CLI -------------------------------------------------------

def test_template_is_untested_and_fails_validation_until_filled() -> None:
    payload = mod.template("idea-1")

    assert payload["idea_status"] == "untested"
    # A template must not silently pass: every REPLACE has to be filled.
    assert mod.validate_idea_evidence(payload)


def test_check_accepts_a_valid_record(tmp_path: Path, capsys) -> None:
    path = tmp_path / "research" / "ideas" / "idea-007" / "EVIDENCE.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record()), encoding="utf-8")

    assert mod.main(["check", "--project-root", str(tmp_path)]) == 0
    assert "1 valid" in capsys.readouterr().out


def test_check_rejects_a_refutation_built_on_an_environment_failure(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "research" / "ideas" / "idea-008" / "EVIDENCE.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            record(
                execution_status="blocked",
                failure_class="environment",
                idea_status="refuted",
            )
        ),
        encoding="utf-8",
    )

    assert mod.main(["check", "--project-root", str(tmp_path)]) == 2
    assert "untested or inconclusive" in capsys.readouterr().err


def test_check_reports_when_there_is_nothing_to_check(tmp_path: Path) -> None:
    assert mod.main(["check", "--project-root", str(tmp_path)]) == 2
