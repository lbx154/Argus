from __future__ import annotations

from pathlib import Path

import pytest

from argus.core.models import ReviewDecision, RunnerResult
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.core.venue_review import (
    ACCEPTANCE_BAR_POLICY,
    current_venue_acceptance_issue,
    enforce_venue_acceptance,
    normalize_venue_review,
    paper_review_snapshot,
    requires_venue_review,
    selected_acceptance_minimum,
    venue_review_issue,
)
from argus.engineer.round_settlement import RoundSettlementMixin
from argus.reviewer import Reviewer, ReviewerConfig
from argus.skills.vertical_select import persist_vertical
from tests.hand_off import hand_off


def assessment(recommendation="weak_accept", *, clear=True, issues=None, venue="ICLR 2027"):
    return {
        "venue": venue, "recommendation": recommendation,
        "acceptance_clear": clear,
        "rationale": "The matched controls establish a useful new systems boundary; the stated scope is supported.",
        "blocking_issues": [] if issues is None else issues,
    }


@pytest.fixture
def paper(tmp_path: Path) -> Path:
    persist_vertical(tmp_path, "research", target_venue="ICLR")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    state["current_verdict"] = "in_progress"
    write_pipeline_state(tmp_path, state)
    (tmp_path / "paper/figures").mkdir(parents=True)
    (tmp_path / "paper/main.tex").write_text("Current manuscript")
    (tmp_path / "paper/main.pdf").write_bytes(b"Current rendered manuscript")
    (tmp_path / "paper/figures/method.pdf").write_bytes(b"Current figure")
    return tmp_path


@pytest.mark.parametrize("rating", ["strong_reject", "reject", "weak_reject", "borderline"])
def test_rejection_cannot_pass_even_with_done(paper, rating):
    review = ReviewDecision(status="done", reason="The local edit is fixed.", next_action="", venue_review=assessment(rating))
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    # The Reviewer's verdict stands; the operator's bar holds the paper open beside it.
    assert review.status == "done"
    assert not review.final_submission_certified
    assert not review.backend_unavailable
    assert review.venue_review["recommendation"] == rating
    assert review.reason == "The local edit is fixed."
    assert review.next_action == ""
    intervention = review.intervention(ACCEPTANCE_BAR_POLICY)
    assert intervention["action"] == "kept_open" and intervention["at"] > 0
    assert f"venue recommendation is {rating}" in intervention["note"]
    assert RoundSettlementMixin._classify(review=review) == (None, "")


def test_invalid_explicit_acceptance_minimum_cannot_silently_lower_the_bar(paper):
    state = read_pipeline_state(paper)
    state["venue_acceptance_minimum"] = "typo"
    write_pipeline_state(paper, state)
    assert "invalid operator" in venue_review_issue(
        assessment("best_paper"), venue="ICLR", minimum=selected_acceptance_minimum(paper),
    )


@pytest.mark.parametrize("report", [assessment(clear=False), assessment(issues=["Missing strongest matched baseline."])])
def test_uncertain_acceptance_or_unresolved_reject_issue_requires_revision(paper, report):
    review = ReviewDecision(status="done", reason="Mostly ready.", next_action="", venue_review=report)
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert review.status == "done"
    assert not review.final_submission_certified
    assert review.intervention(ACCEPTANCE_BAR_POLICY)


def test_manuscript_change_during_review_cannot_be_certified(paper):
    before = paper_review_snapshot(paper)
    (paper / "paper/main.pdf").write_bytes(b"Concurrent new PDF")
    review = ReviewDecision(status="done", reason="Accepted old PDF.", next_action="", venue_review=assessment())
    enforce_venue_acceptance(review, venue="ICLR", before=before, artifact_root=paper)
    assert review.status == "done"
    assert not review.final_submission_certified
    assert "changed during review" in review.intervention(ACCEPTANCE_BAR_POLICY)["note"]


def test_a_final_reviews_own_routing_stands(paper):
    review = ReviewDecision(
        status="planner", reason="The baseline processes fewer positions.",
        next_action="Measure latency and accepted tokens under the same speculative budget.",
        venue_review=assessment("weak_reject", clear=False),
    )
    enforce_venue_acceptance(review, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert review.status == "planner"
    assert review.next_action == "Measure latency and accepted tokens under the same speculative budget."
    assert not review.interventions
    assert not review.final_submission_certified


def test_a_review_keeps_its_rating_but_not_a_model_claimed_binding():
    report = {**assessment(), "sha256": "model-invented"}
    assert normalize_venue_review(report) == assessment()
    assert normalize_venue_review({**report, "recommendation": "borderline_accept"}) is None


class _Runner:
    backend = "pi"

    def __init__(self, report):
        self.report = report
        self.prompt = ""

    def run_exec(self, **kwargs):
        self.prompt = kwargs["prompt"]
        payload = hand_off("done", "Assessed current paper.")[1]
        if self.report:
            payload["recommendation"] = self.report["recommendation"]
        bridge_request(PREFIX, "hand_off", payload, env=kwargs["options"].extension_env)
        return RunnerResult(exit_code=0)


def test_integrated_reviewer_persists_the_actual_venue_recommendation(paper):
    review = Reviewer(_Runner(assessment())).evaluate(
        objective="Certify the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), vertical_state_root=str(paper)),
    )
    assert review.final_submission_certified
    assert "Recommendation: weak_accept" in (paper / "paper/REVIEW.md").read_text()
    assert current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper) == ""


def test_reviewer_uses_the_selected_venue_from_a_separate_state_directory(paper):
    state_root = paper / "session-state"
    persist_vertical(state_root, "research", target_venue="ICLR")
    state = read_pipeline_state(state_root)
    state["current_stage"] = "review"
    write_pipeline_state(state_root, state)
    review = Reviewer(_Runner(assessment())).evaluate(
        objective="Certify the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), artifact_root=str(paper), vertical_state_root=str(state_root)),
    )
    assert review.final_submission_certified
    assert current_venue_acceptance_issue(review, state_root=state_root, artifact_root=paper) == ""


def test_formal_reviewer_gets_the_operator_bar_from_control_state(paper):
    state_root = paper / "session-state"
    persist_vertical(state_root, "research", target_venue="ICLR")
    state = read_pipeline_state(state_root)
    state.update(current_stage="review", venue_acceptance_minimum="strong_accept")
    write_pipeline_state(state_root, state)
    runner = _Runner(assessment())
    config = ReviewerConfig(
        active_vertical="research", working_dir=str(paper),
        artifact_root=str(paper), vertical_state_root=str(state_root),
    )
    review = Reviewer(runner).evaluate(
        objective="Improve this paper to strong acceptance", round_index=1,
        session_id=None, main_summary="Stronger evidence added.", main_error=None,
        scope="final_submission", config=config,
    )
    assert "completion bar is strong accept" in runner.prompt
    assert "recommendation: clearly weak_accept or better" not in runner.prompt
    assert review.status == "done"
    assert not review.final_submission_certified
    assert review.intervention(ACCEPTANCE_BAR_POLICY)
    assert "Recommendation: weak_accept" in (paper / "paper/REVIEW.md").read_text()
    prior_fingerprint = review.static_fingerprint

    state["venue_acceptance_minimum"] = "weak_accept"
    write_pipeline_state(state_root, state)
    review = Reviewer(runner).evaluate(
        objective="Judge the current paper", round_index=2, session_id=None,
        main_summary="No paper changes", main_error=None, scope="final_submission",
        config=config, resume_thread_id="old-session", prior_static_fingerprint=prior_fingerprint,
    )
    assert review.final_submission_certified
    assert review.static_fingerprint != prior_fingerprint
    assert not review.session_resumed


def test_unclassified_nonpaper_review_does_not_invent_a_venue_requirement(tmp_path):
    review = Reviewer(_Runner(None)).evaluate(
        objective="Certify the implementation", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", working_dir=str(tmp_path)),
    )
    assert review.status == "done"
    assert not review.venue_review_required


def test_a_final_acceptance_of_a_survey_is_its_ordinary_review(tmp_path):
    # The survey run of 2026-10-24: a research final acceptance with no paper
    # in the workspace. No venue recommendation could ever bind to it, so the
    # Reviewer's pass, with a note on Argus's own records, certifies it.
    (tmp_path / "reward-hacking-survey-zh.md").write_text("# 综述\n", encoding="utf-8")
    assert not requires_venue_review(
        vertical="research", stage="idea", scope="final_submission", artifact_root=tmp_path,
    )
    runner = _Runner(None)
    review = Reviewer(runner).evaluate(
        objective="Final acceptance of reward-hacking-survey-zh.md", round_index=1, session_id=None,
        main_summary="source-verification.md line 55 misattributes a wording", main_error=None,
        scope="final_submission",
        config=ReviewerConfig(
            model="gpt-5.6-sol", active_vertical="research", working_dir=str(tmp_path),
            artifact_root=str(tmp_path), vertical_state_root=str(tmp_path),
        ),
    )
    assert review.status == "done"
    assert not review.venue_review_required
    assert review.final_submission_certified


def test_settlement_follows_the_verdict_and_the_operator_bar(paper):
    rejecting = ReviewDecision(status="engineer", reason="Still below weak accept.", next_action="Run the missing matched control.")
    assert RoundSettlementMixin._classify(review=rejecting) == (None, "")
    stopped = ReviewDecision(status="operator", reason="Operator stopped this work.", next_action="", operator_question="Wait for the operator.")
    assert RoundSettlementMixin._classify(review=stopped)[0] == "operator"
    accepted = ReviewDecision(status="done", reason="Accepted.", next_action="", venue_review=assessment())
    enforce_venue_acceptance(accepted, venue="ICLR", before=paper_review_snapshot(paper), artifact_root=paper)
    assert RoundSettlementMixin._classify(review=accepted)[0] == "done"


@pytest.mark.parametrize("recommendation", ["weak_accept", "weak_reject"])
def test_reviewer_verdict_counts_when_no_venue_was_selected(paper, recommendation):
    from types import SimpleNamespace

    from argus.core.stage_certificate import latest_stage_review, record_stage_review

    state_root = paper
    state = read_pipeline_state(state_root)
    state.pop("target_venue", None)
    state.pop("venue", None)
    write_pipeline_state(state_root, state)
    runner = _Runner(assessment(recommendation))
    review = Reviewer(runner).evaluate(
        objective="Judge the current paper", round_index=1, session_id=None,
        main_summary="Ready for review", main_error=None, scope="final_submission",
        config=ReviewerConfig(model="gpt-5.6-sol", active_vertical="research", working_dir=str(paper), artifact_root=str(paper), vertical_state_root=str(state_root)),
    )
    assert "No venue has been selected for this paper" in runner.prompt
    assert "(not selected)" not in runner.prompt
    # A verdict against the stated standard is a verdict: never a Reviewer backend failure.
    assert not review.backend_unavailable
    assert "omitted a valid assessment" not in (review.reason or "")
    # No venue name is invented for the record.
    assert review.venue_review["venue"] == ""
    assert review.venue_review["recommendation"] == recommendation
    report = (paper / "paper/REVIEW.md").read_text()
    assert f"Recommendation: {recommendation}" in report
    accepted = recommendation == "weak_accept"
    assert review.final_submission_certified is accepted
    # The finalizers apply the same check: an accepting verdict lets the project finish.
    issue = current_venue_acceptance_issue(review, state_root=state_root, artifact_root=paper)
    assert (issue == "") is accepted
    record_stage_review(
        state_root=state_root, project_root=paper, stage="review", item=SimpleNamespace(id="final-review"),
        manager_action="complete", venue_review=review.venue_review,
        venue_review_snapshot=review.venue_review_snapshot,
    )
    assert latest_stage_review(state_root, "review")["certified"] is accepted
    if not accepted:
        assert "selected venue" not in issue
        assert "selected venue" not in review.next_action


class _PaperEngineer:
    """An Engineer that leaves the paper as it is."""

    def __init__(self) -> None:
        self.calls = 0

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):  # noqa: ARG002
        self.calls += 1
        return RunnerResult(exit_code=0, agent_messages=["Looked the paper over; ready for review."])


class _BelowBarReviewer:
    """A paid venue verdict below the bar, bound to the paper it read."""

    def __init__(self, paper: Path) -> None:
        self.paper, self.calls = paper, 0

    def evaluate(self, **_kwargs):
        self.calls += 1
        return ReviewDecision(
            status="engineer", reason="Still below weak accept.", next_action="Add the matched control.",
            venue_review_required=True, venue_review_snapshot=paper_review_snapshot(self.paper),
        )


def _final_review(paper: Path, state: Path):
    from typing import Any, cast

    from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer

    engineer, reviewer = _PaperEngineer(), _BelowBarReviewer(paper)
    supervised = cast(Any, SupervisedEngineer.__new__(SupervisedEngineer))
    supervised.engineer_runner, supervised.engineer_config = engineer, EngineerConfig(model="stub")
    supervised.reviewer, supervised.reviewer_config = reviewer, ReviewerConfig(model="stub")
    config = SupervisedConfig(
        checkpoint_path=paper / "CHECKPOINT.md", state_root=state, session_id="paper",
    )
    status, _rounds, _final, reason, _thread = supervised.run(
        objective="Bring the paper to the venue bar in final Review",
        engineer_prompt_builder=lambda next_action, include_static=True: "Revise. " + (next_action or ""),
        supervised_config=config, workdir=paper,
    )
    return status, reason, engineer.calls, reviewer.calls


def test_an_unchanged_paper_is_not_reviewed_again(paper, tmp_path_factory):
    # The baton's third invariant: the Engineer reads the review once; the
    # paper coming back unchanged passes the Reviewer and climbs to the Planner.
    status, reason, engineer_calls, reviewer_calls = _final_review(paper, tmp_path_factory.mktemp("state"))
    assert (engineer_calls, reviewer_calls) == (2, 1)
    assert status == "planner"
    assert reason.startswith("This path is making no new progress")
