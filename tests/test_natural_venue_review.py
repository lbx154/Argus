from __future__ import annotations

import json

import pytest

from argus.core.models import RunnerResult
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.core.role_tool_bridge import bridge_request
from argus.core.venue_review import current_venue_acceptance_issue
from argus.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
    render_mission_brief,
)
from argus.reviewer import Reviewer, ReviewerConfig
from argus.skills.vertical_select import persist_vertical

ACCEPTANCE = "作为 ICLR 审稿人，我对当前版本的明确建议是 weak accept。"
FEEDBACK = ACCEPTANCE + "修改后再审。" + "比较残差分布并保留原始种子结果。" * 250


@pytest.fixture
def paper(tmp_path):
    persist_vertical(tmp_path, "research", target_venue="ICLR")
    state = read_pipeline_state(tmp_path)
    state["current_stage"] = "review"
    write_pipeline_state(tmp_path, state)
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper/main.tex").write_text("Current manuscript")
    (tmp_path / "paper/main.pdf").write_bytes(b"Current PDF")
    return tmp_path


class ProseRunner:
    backend = "pi"

    def __init__(self, prose, action="revise_review", recommendation="weak_accept", **arguments):
        self.prose = prose
        self.action = action
        self.recommendation = recommendation
        self.arguments = arguments
        self.calls = []

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["run_label"] == "reviewer", "No JSON translation call is allowed"
        if self.action:
            payload = {"review": self.prose, **self.arguments}
            if self.action in {"approve_review", "revise_review"}:
                payload["recommendation"] = self.recommendation
            bridge_request("ARGUS_PLUGIN_REVIEW", self.action, payload, env=kwargs["options"].extension_env)
        return RunnerResult(exit_code=0, agent_messages=["审查意见已提交。"], input_tokens=100, output_tokens=50)


def evaluate(paper, runner):
    return Reviewer(runner).evaluate(
        objective="精修当前论文，依据最终审稿反馈继续提升。", round_index=1,
        session_id=None, main_summary="已修订方法解释。", main_error=None,
        scope="final_submission",
        config=ReviewerConfig(
            model="gpt-5.6-sol", active_vertical="research",
            working_dir=str(paper), vertical_state_root=str(paper),
        ),
    )


def test_natural_feedback_survives_action_handoff_and_engineer_reentry(paper):
    runner = ProseRunner(FEEDBACK)
    review = evaluate(paper, runner)
    assert review.status == "continue" and not review.backend_unavailable
    assert not review.final_submission_certified
    assert review.reason == review.next_action == FEEDBACK
    assert review.input_tokens == 100 and review.output_tokens == 50
    assert len(runner.calls) == 1
    assert "improvements" in current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)
    for marker in ("STATUS=", "REASON=", "VENUE_REVIEW=", "RESEARCH_RESULT=<JSON>"):
        assert marker not in runner.calls[0]["prompt"]
    mission = create_mission_context(
        life_dir=paper / "state", mission_id="refinement", stage="review",
        objective="Improve the current paper", scope="final_submission", execution_workdir=str(paper),
    )
    handoff = record_reviewed_handoff(
        mission_context_path=mission, round_index=1, engineer_summary="Revised.",
        review=review, checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    assert json.loads(handoff.read_text())["review"]["next_action"] == FEEDBACK
    assert str(handoff) in render_mission_brief(mission)
    assert (paper / "paper/REVIEW.md").read_text().count(FEEDBACK) == 1


def test_explicit_approval_can_finish_and_is_bound_to_current_paper(paper):
    prose = ACCEPTANCE + "必要修改已完成；扩大模型家族留作后续工作。"
    review = evaluate(paper, ProseRunner(prose, "approve_review"))
    assert review.final_submission_certified and review.reason == prose
    assert review.next_action == ""
    (paper / "paper/main.pdf").write_bytes(b"Changed after review")
    assert "changed" in current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)


def test_minimum_rating_cannot_be_satisfied_by_aspirational_prose(paper):
    state = read_pipeline_state(paper)
    state["venue_acceptance_minimum"] = "strong_accept"
    write_pipeline_state(paper, state)
    prose = ACCEPTANCE + "未来有望达到 strong accept。"
    result = evaluate(paper, ProseRunner(prose, "approve_review"))
    assert result.status == "continue" and not result.final_submission_certified
    assert result.venue_review["recommendation"] == "weak_accept"
    assert result.reason == prose
    result = evaluate(paper, ProseRunner("当前版本达到 strong accept。", "approve_review", "strong_accept"))
    assert result.final_submission_certified


def test_reviewer_owned_file_stays_authoritative(paper):
    from argus.reviewer.review_file import ReviewFileStore

    class WritingReviewer(ProseRunner):
        backend = "copilot"

        def run_exec(self, **kwargs):
            ReviewFileStore(**kwargs["options"].review_output).write_review(self.prose)
            return super().run_exec(**kwargs)

    runner = WritingReviewer(FEEDBACK)
    result = evaluate(paper, runner)
    assert result.reason == FEEDBACK and result.status == "continue"
    assert (paper / "paper/REVIEW.md").read_text() == FEEDBACK
    runner.prose = ACCEPTANCE + "新增实验解决了此前问题，无需返修。"
    runner.action = "approve_review"
    result = evaluate(paper, runner)
    assert result.final_submission_certified
    assert (paper / "paper/REVIEW.md").read_text() == runner.prose


def test_below_threshold_recommendation_cannot_pass_an_approval_action(paper):
    result = evaluate(paper, ProseRunner("当前只有 borderline。", "approve_review", "borderline"))
    assert result.status == "continue" and not result.final_submission_certified
    assert not result.backend_unavailable


@pytest.mark.parametrize(("action", "arguments", "status"), [
    ("defer_review", {}, "continue"),
    ("request_review_decision", {"question": "可否使用授权数据集？"}, "blocked"),
    ("replan_review", {"authority_impact": "technical", "alternative": "补充分组实验。"}, "continue"),
])
def test_wait_questions_and_scientific_repairs_are_not_backend_failure(paper, action, arguments, status):
    feedback = "需要等后台实验，或解决所述前提，再作最终判断。"
    result = evaluate(paper, ProseRunner(feedback, action, **arguments))
    assert result.status == status and not result.backend_unavailable
    assert not result.final_submission_certified
    assert result.reason == feedback
    assert read_pipeline_state(paper)["current_stage"] == "review"


def test_acknowledgement_cannot_reuse_preexisting_acceptance_report(paper):
    report = paper / "paper/REVIEW.md"
    report.write_text(ACCEPTANCE)
    runner = ProseRunner("已更新我的审稿文件。", action=None)
    runner.backend = "copilot"
    review = evaluate(paper, runner)
    assert review.backend_unavailable and not review.final_submission_certified
    assert report.read_text() == ACCEPTANCE
