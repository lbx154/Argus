"""Reviewer JSON parsing — vendored test surface, sanity check."""
from __future__ import annotations

import json

from argus_skill.engineer.reviewer import parse_decision_text


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
