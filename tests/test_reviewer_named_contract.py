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
        "CHECKPOINT_RECOMMENDED=false\n"
        "FORWARD_PROGRESS=false\n"
        "PLAN_SIGNAL=continue\n"
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert "second material fact" in decision.reason
    assert decision.next_action == "Collect the missing evidence."
    assert decision.operator_question == "Which route?"


def test_named_verdict_fails_closed_without_status_or_reason() -> None:
    assert parse_decision_text("REASON=Missing status\nNEXT_ACTION=retry") is None
    assert parse_decision_text("STATUS=done\nREASON=\nNEXT_ACTION=") is None


def test_legacy_json_verdict_still_parses_for_inflight_sessions() -> None:
    decision = parse_decision_text(
        '{"status":"blocked","reason":"Need operator input.",'
        '"next_action":"","operator_question":"Which route?",'
        '"checkpoint_recommended":false}'
    )

    assert decision is not None
    assert decision.status == "blocked"
    assert decision.operator_question == "Which route?"


def test_retired_reviewer_output_schema_assets_are_absent() -> None:
    package = Path(reviewer_core.__file__).resolve().parent
    assert list(package.glob("reviewer*_schema.json")) == []
