from __future__ import annotations

from pathlib import Path

import argus_skill.reviewer._core as reviewer_core
from argus_skill.reviewer import parse_decision_text


def test_named_reviewer_verdict_parses_with_multiline_reason() -> None:
    decision = parse_decision_text(
        "STATUS=blocked\n"
        "REASON=The evidence is incomplete.\n"
        "A second material fact is also missing.\n"
        "NEXT_ACTION=Collect the missing evidence.\n"
        "OPERATOR_QUESTION=Which route?\n"
        "OPERATOR_OPTIONS=collect-logs :: Collect logs :: "
        "Collect the missing provider logs.\n"
        "CHECKPOINT_RECOMMENDED=false\n"
        "FORWARD_PROGRESS=false\n"
        "PLAN_SIGNAL=continue\n"
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert "second material fact" in decision.reason
    assert decision.next_action == "Collect the missing evidence."
    assert decision.operator_question == "Which route?"
    assert decision.operator_options == [{
        "id": "collect-logs",
        "label": "Collect logs",
        "description": "Collect the missing provider logs.",
        "requires_note": False,
    }]


def test_named_verdict_fails_closed_without_status_or_reason() -> None:
    assert parse_decision_text("REASON=Missing status\nNEXT_ACTION=retry") is None
    assert parse_decision_text("STATUS=done\nREASON=\nNEXT_ACTION=") is None


def test_natural_verdict_label_recovers_a_missing_status_line() -> None:
    decision = parse_decision_text(
        "Verdict: continue.\n"
        "REASON=The final implementation step remains.\n"
        "NEXT_ACTION=Finish the current step.\n"
        "OPERATOR_QUESTION=none\n"
    )

    assert decision is not None
    assert decision.status == "continue"
    assert decision.next_action == "Finish the current step."


def test_legacy_json_verdict_still_parses_for_inflight_sessions() -> None:
    decision = parse_decision_text(
        '{"status":"blocked","reason":"Need operator input.",'
        '"next_action":"","operator_question":"Which route?",'
        '"operator_options":[{"id":"route-a","label":"Route A",'
        '"description":"Take route A.","requires_note":true}],'
        '"checkpoint_recommended":false}'
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.operator_question == "Which route?"
    assert decision.operator_options[0]["label"] == "Route A"
    assert decision.operator_options[0]["requires_note"] is True


def test_named_reviewer_verdict_parses_research_result_contract() -> None:
    decision = parse_decision_text(
        "STATUS=done\n"
        "REASON=The literature synthesis is supported by the cited sources.\n"
        "NEXT_ACTION=\n"
        "RESEARCH_RESULT={\"result_class\":\"literature_review\","
        "\"correctness_status\":\"verified\",\"novelty_status\":\"known\","
        "\"significance_status\":\"publishable\","
        "\"statement_fidelity_status\":\"verified\","
        "\"evidence\":[\"source audit\"],\"limitations\":[]}\n"
        "FORWARD_PROGRESS=true\n"
    )

    assert decision is not None
    assert decision.research_result is not None
    assert decision.research_result["result_class"] == "literature_review"
    assert decision.research_result["significance_status"] == "publishable"


def test_retired_reviewer_output_schema_assets_are_absent() -> None:
    package = Path(reviewer_core.__file__).resolve().parent
    assert list(package.glob("reviewer*_schema.json")) == []
