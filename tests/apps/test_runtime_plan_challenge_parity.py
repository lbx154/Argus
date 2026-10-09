"""A Reviewer replan with an alternative routes through the runtime as on dev.

This change routes operator *questions* by the raising role's own label, but
leaves plan-challenge routing exactly as it was. These cases pin the outcome
the runtime records for a replan that carries an alternative, with an
operator present, to dev's.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.apps._runtime_execute import SkillLoopExecuteMixin
from argus.apps._runtime_helpers import _ExecuteState
from argus.core.models import ReviewDecision


class _Harness(SkillLoopExecuteMixin):
    def __init__(self) -> None:
        self.last_thread_id = None
        self._next_seed_thread_id = None
        self.manager = None

    def _consume_auth_failure(self):
        return None


def _route(tmp_path: Path, report: dict, *, question: str = "") -> dict:
    review = ReviewDecision(
        status="replan_requested",
        reason=str(report.get("challenge") or "The plan is challenged."),
        next_action=str(report.get("alternative") or ""),
        operator_question=question,
        planner_report=dict(report),
    )
    state = _ExecuteState()
    state.workdir = tmp_path
    state.mission_scope = ""
    state.outcome = SimpleNamespace(
        status="replan_requested",
        last_thread_id="t1",
        stop_reason="",
        stop_kind=None,
        rounds=[SimpleNamespace(review=review)],
    )
    _Harness()._extract_execute_outcome_fields(state)
    return {
        key: state.plan_challenge.get(key)
        for key in ("manager_action", "alternative", "authority_impact", "source")
    }


# Expected values are dev's (origin/dev before this change) for the same input.
@pytest.mark.parametrize(
    ("report", "question", "expected"),
    [
        (
            {"authority_impact": "technical", "challenge": "pandas is slow here",
             "alternative": "Load the data with polars instead of pandas."},
            "pandas or polars?",
            ("replace", "technical"),
        ),
        (
            {"authority_impact": "technical", "challenge": "the run is too big",
             "alternative": "Reduce the dataset size to the first 10k rows."},
            "reduce dataset size?",
            ("replace", "technical"),
        ),
        (
            {"authority_impact": "technical", "challenge": "two spec lines conflict",
             "alternative": "Follow the spec line in section 2."},
            "which spec line wins?",
            ("replace", "technical"),
        ),
        (
            {"authority_impact": "operator", "challenge": "acceptance would change",
             "alternative": "Relax the acceptance threshold."},
            "",
            ("ask_operator", "operator"),
        ),
        (
            # dev's own boundary check still guards an alternative.
            {"authority_impact": "technical", "challenge": "the release is broken",
             "alternative": "Force-push the protected release branch."},
            "Should we fix the release?",
            ("ask_operator", "operator"),
        ),
    ],
)
def test_replan_with_alternative_matches_dev_with_an_operator(
    tmp_path, monkeypatch, report, question, expected
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    monkeypatch.setenv("ARGUS_SKILL_AUTONOMY_MODE", "pragmatic")
    routed = _route(tmp_path, report, question=question)
    assert (routed["manager_action"], routed["authority_impact"]) == expected
    assert routed["alternative"] == report["alternative"]
