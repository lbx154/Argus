"""A Reviewer replan with an alternative replaces the plan.

These cases pin the outcome the runtime records for a replan that carries an
alternative, with an operator present: words in the challenge or the
alternative never route it elsewhere.
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
        for key in ("manager_action", "alternative", "source")
    }


@pytest.mark.parametrize(
    ("report", "question"),
    [
        (
            {"challenge": "pandas is slow here",
             "alternative": "Load the data with polars instead of pandas."},
            "pandas or polars?",
        ),
        (
            {"challenge": "the run is too big",
             "alternative": "Reduce the dataset size to the first 10k rows."},
            "reduce dataset size?",
        ),
        (
            {"challenge": "two spec lines conflict",
             "alternative": "Follow the spec line in section 2."},
            "which spec line wins?",
        ),
        (
            # Alarming words in a technical alternative do not decide.
            {"challenge": "the release is broken",
             "alternative": "Force-push the protected release branch."},
            "Should we fix the release?",
        ),
    ],
)
def test_replan_with_alternative_matches_dev_with_an_operator(
    tmp_path, monkeypatch, report, question
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_OPERATOR_AVAILABLE", "true")
    routed = _route(tmp_path, report, question=question)
    assert routed["manager_action"] == "replace"
    assert routed["alternative"] == report["alternative"]
