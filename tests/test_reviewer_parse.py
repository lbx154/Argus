"""Reviewer JSON parsing — vendored test surface, sanity check."""
from __future__ import annotations

import json

from argus_skill.core.models import CheckResult
from argus_skill.engineer.reviewer import Reviewer, parse_decision_text


class _UnusedRunner:
    def run_exec(self, **kwargs):  # noqa: ANN003
        raise AssertionError("prompt construction test should not invoke runner")


def test_parse_clean_json() -> None:
    payload = json.dumps({
        "status": "done",
        "confidence": 0.9,
        "reason": "All checks pass.",
        "next_action": "No further action needed.",
        "round_summary_markdown": "# Review\n\n- ok\n",
        "completion_summary_markdown": "Done.",
    })
    decision = parse_decision_text(payload)
    assert decision is not None
    assert decision.status == "done"
    assert decision.confidence == 0.9
    assert decision.reason == "All checks pass."


def test_parse_with_markdown_fences() -> None:
    payload = "```json\n" + json.dumps({
        "status": "continue",
        "confidence": 0.5,
        "reason": "Not yet done.",
        "next_action": "Add tests.",
        "round_summary_markdown": "# Review\n\n- partial\n",
        "completion_summary_markdown": "",
    }) + "\n```"
    decision = parse_decision_text(payload)
    assert decision is not None
    assert decision.status == "continue"


def test_parse_extracts_inner_json_from_chatter() -> None:
    payload = (
        "Sure, here is my decision:\n\n"
        + json.dumps({
            "status": "blocked",
            "confidence": 1.0,
            "reason": "Need credential.",
            "next_action": "Provide API key.",
            "round_summary_markdown": "# Review\n\n- blocked\n",
            "completion_summary_markdown": "",
        })
        + "\n\nLet me know if anything else is needed."
    )
    decision = parse_decision_text(payload)
    assert decision is not None
    assert decision.status == "blocked"


def test_parse_rejects_garbage() -> None:
    assert parse_decision_text("not json") is None
    assert parse_decision_text("{ no status here }") is None


def test_parse_rejects_invalid_status() -> None:
    payload = json.dumps({
        "status": "maybe",
        "confidence": 0.5,
        "reason": "?",
        "next_action": "?",
        "round_summary_markdown": "?",
        "completion_summary_markdown": "",
    })
    assert parse_decision_text(payload) is None


def test_parse_rejects_out_of_range_confidence() -> None:
    payload = json.dumps({
        "status": "done",
        "confidence": 1.5,
        "reason": "?",
        "next_action": "?",
        "round_summary_markdown": "?",
        "completion_summary_markdown": "",
    })
    assert parse_decision_text(payload) is None


def test_reviewer_prompt_teaches_handoff_and_marks_checks_as_reviewer_only() -> None:
    reviewer = Reviewer(_UnusedRunner())
    prompt = reviewer._build_prompt(
        objective="final_submission paper",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="engineer claims success",
        main_error=None,
        checks=[
            CheckResult(
                command="python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .",
                exit_code=1,
                passed=False,
                output_tail=(
                    "image2_conceptual_figure_not_included_in_main_tex "
                    "paper/main.tex paper/figures/method.png"
                ),
            )
        ],
    )

    assert "Reviewer-to-engineer handoff skill:" in prompt
    assert "Treat validation output as reviewer-only evidence" in prompt
    assert "gpt-5.4-mini" in prompt
    assert "Acceptance check results (reviewer-only evidence" in prompt
    assert "summarize into `next_action`, do not paste raw output wholesale" in prompt
    assert "image2_conceptual_figure_not_included_in_main_tex" in prompt
