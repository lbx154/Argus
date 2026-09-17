"""The daemon outcome carries the manuscript the final Reviewer read.

``_Outcome.rounds`` is a count, so settlement could never recover the
Reviewer's manuscript binding from it. Every certified final submission was
journaled without a binding, and the research completion check skipped it
forever. FuseHead's Manager-certified paper was held on exactly that on
2026-09-06.
"""
from __future__ import annotations

from types import SimpleNamespace

from argus.apps._runtime_backends import _Outcome
from argus.life.supervisor._mission_execution_settlement import (
    outcome_manuscript_binding,
)

_BINDING = {
    "path": "paper/main.tex",
    "sha256": "a" * 64,
    "recorded_at": "2026-09-06T10:33:01+00:00",
}


def test_daemon_outcome_carries_the_binding_even_though_rounds_is_a_count() -> None:
    outcome = _Outcome(
        success=True,
        status="done",
        rounds=2,
        final_submission_certified=True,
        manuscript_snapshot=dict(_BINDING),
    )
    assert outcome_manuscript_binding(outcome) == _BINDING


def test_round_record_outcome_still_yields_the_final_review_binding() -> None:
    outcome = SimpleNamespace(
        rounds=[SimpleNamespace(review=SimpleNamespace(manuscript_snapshot=dict(_BINDING)))],
    )
    assert outcome_manuscript_binding(outcome) == _BINDING


def test_missing_or_empty_binding_is_none() -> None:
    assert outcome_manuscript_binding(_Outcome(success=True, status="done", rounds=3)) is None
    assert (
        outcome_manuscript_binding(
            SimpleNamespace(rounds=[SimpleNamespace(review=SimpleNamespace(manuscript_snapshot={"sha256": ""}))])
        )
        is None
    )
