from __future__ import annotations

import json

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.core.venue_review import current_venue_acceptance_issue
from argus_skill.life.context_packet import (
    create_mission_context,
    record_reviewed_handoff,
    render_mission_brief,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig
from argus_skill.reviewer._prose_decision import decision_from_prose_control
from argus_skill.skills.vertical_select import persist_vertical

ACCEPTANCE = "作为 ICLR 审稿人，我对当前版本的明确建议是 weak accept。"
FEEDBACK = (
    ACCEPTANCE + "配对对照使边界结论有了可信的基础，这轮解释也更清楚了。"
    "要继续推向更强的论文，建议先做下面的机制对照，修改后再审。"
    + "在固定信息量下随机打乱分组，比较残差分布并保留原始种子结果。" * 150
    + "最终验收：跨种子的置信区间需要能区分分组效应与实现开销；若不能区分，应保留负结果并修正解释。"
)


def control(*, revision=True, status="continue", quote=ACCEPTANCE):
    return {
        "status": status,
        "venue_review": {
            "venue": "ICLR", "recommendation": "weak_accept",
            "acceptance_clear": True, "rationale": quote, "blocking_issues": [],
            "revision_required": revision,
        },
        "recommendation_quote": quote,
        "operator_question": "",
    }


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

    def __init__(self, prose, extracted):
        self.prose = prose
        self.extracted = extracted
        self.calls = []

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps(self.extracted, ensure_ascii=False) if kwargs["run_label"] == "reviewer_control" else self.prose
        return RunnerResult(exit_code=0, agent_messages=[text], input_tokens=100, output_tokens=50)


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


def test_natural_final_feedback_survives_parse_handoff_and_engineer_reentry(paper):
    runner = ProseRunner(FEEDBACK, control(status="done"))
    review = evaluate(paper, runner)
    assert review.status == "continue"
    assert not review.final_submission_certified
    assert not review.backend_unavailable
    assert review.next_action == FEEDBACK
    assert review.input_tokens == 200 and review.output_tokens == 100
    assert "improvements" in current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)
    assert "STATUS=done" not in runner.calls[0]["prompt"]
    assert "VENUE_REVIEW=" not in runner.calls[0]["prompt"]
    assert "RESEARCH_RESULT=<JSON>" not in runner.calls[0]["prompt"]
    assert runner.calls[1]["options"].disable_tools
    assert runner.calls[1]["options"].force_safe_mode
    assert runner.calls[1]["resume_thread_id"] is None
    mission = create_mission_context(
        life_dir=paper / "state", mission_id="refinement", stage="review",
        objective="Improve the current paper", scope="final_submission",
        execution_workdir=str(paper),
    )
    handoff = record_reviewed_handoff(
        mission_context_path=mission, round_index=1, engineer_summary="Revised.",
        review=review, checkpoint_path=mission.parent / "CHECKPOINT.md",
    )
    assert handoff is not None
    saved = json.loads(handoff.read_text())
    assert saved["review"]["next_action"] == FEEDBACK
    assert str(handoff) in render_mission_brief(mission)
    assert FEEDBACK in (paper / "paper/REVIEW.md").read_text()
    assert "## Reject-level issues\nNone." in (paper / "paper/REVIEW.md").read_text()


def test_clear_natural_acceptance_with_only_optional_future_work_can_finish(paper):
    prose = ACCEPTANCE + "验收所需的修改已经完成。扩大到新的模型家族可以留作后续工作。"
    review = evaluate(paper, ProseRunner(prose, control(revision=False, status="done")))
    assert review.final_submission_certified
    assert review.reason == prose
    assert review.next_action == ""
    (paper / "paper/main.pdf").write_bytes(b"Changed after review")
    assert "changed" in current_venue_acceptance_issue(review, state_root=paper, artifact_root=paper)


def test_natural_rejection_cannot_pass_an_adapter_done(paper):
    quote = "当前版本在 ICLR 只能判 borderline，还没有达到明确接收的标准。"
    extracted = control(revision=False, status="done", quote=quote)
    extracted["venue_review"]["recommendation"] = "borderline"
    extracted["venue_review"]["acceptance_clear"] = False
    review = evaluate(paper, ProseRunner(quote, extracted))
    assert review.status == "continue"
    assert not review.final_submission_certified
    assert not review.backend_unavailable


@pytest.mark.parametrize("bad", [None, control(quote="A fabricated recommendation"), {"status": "done"}])
def test_unreadable_or_invented_control_retries_reviewer_only(paper, bad):
    review = evaluate(paper, ProseRunner(FEEDBACK, bad))
    assert review.backend_unavailable
    assert not review.final_submission_certified
    assert "Retry the independent Reviewer" in review.next_action
    assert not (paper / "paper/REVIEW.md").exists()


def test_internal_reader_cannot_invent_an_operator_question_or_blocker():
    extracted = control()
    extracted["operator_question"] = "May I lower the venue?"
    assert decision_from_prose_control(extracted, review_text=FEEDBACK) is None
    extracted["operator_question"] = ""
    extracted["venue_review"]["blocking_issues"] = ["Invented scientific defect"]
    assert decision_from_prose_control(extracted, review_text=FEEDBACK) is None
