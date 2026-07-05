"""Tests for the Reviewer's ``wiki_ops`` parsing — the wiki's structured
counterpart to ``skill_ops``. Fail-soft: a malformed entry is dropped, an
unknown ``op`` is dropped, and a non-list value yields ``[]`` so the loop
simply applies nothing. The WikiRouter (not this parser) verifies evidence
spans quote an immutable source verbatim — this parser only shapes the data.
"""
from __future__ import annotations

import json

from argus_skill.reviewer._parsing import parse_decision_text


def _review_json(**over):
    base = {
        "status": "continue",
        "reason": "r",
        "next_action": "na",
        "round_summary_markdown": "# x",
        "completion_summary_markdown": "",
        "failure_cause": None,
        "scope": None,
        "planner_report": {"forward_progress": False, "headline": "h", "blocker": "",
                           "recommended_next": "", "evidence_files": []},
        "checklist": [],
        "checkpoint": {"goal": "g", "done": [], "tried_and_failed": [], "maturing": [],
                       "open_blocker": "", "next_step": "", "active_line": None, "env_facts": []},
        "skill_ops": [],
        "wiki_ops": [],
        "checklist_feedback": None,
    }
    base.update(over)
    return json.dumps(base)


def test_absent_wiki_ops_is_empty_list():
    d = parse_decision_text(_review_json())
    assert d is not None and d.wiki_ops == []


def test_null_wiki_ops_is_empty_list():
    d = parse_decision_text(_review_json(wiki_ops=None))
    assert d is not None and d.wiki_ops == []


def test_non_list_wiki_ops_is_empty_list():
    d = parse_decision_text(_review_json(wiki_ops={"op": "create_page"}))
    assert d is not None and d.wiki_ops == []


def test_create_page_parsed_with_evidence():
    d = parse_decision_text(_review_json(wiki_ops=[{
        "op": "create_page", "id": "grpo-async-clip", "card_type": "technique",
        "title": "Async clip", "status": "scratch", "body": "the trick",
        "evidence": [{"source_id": "grpo-tricks", "quote": "clips the ratio",
                      "locator": "p1"}],
        "why": "reusable",
    }]))
    assert len(d.wiki_ops) == 1
    op = d.wiki_ops[0]
    assert op["op"] == "create_page"
    assert op["id"] == "grpo-async-clip"
    assert op["card_type"] == "technique"
    assert op["status"] == "scratch"
    assert op["body"] == "the trick"
    assert op["evidence"] == [
        {"source_id": "grpo-tricks", "quote": "clips the ratio", "locator": "p1"}
    ]


def test_retire_page_parsed_without_body_or_evidence():
    d = parse_decision_text(_review_json(wiki_ops=[{
        "op": "retire_page", "id": "stale-page", "why": "superseded",
    }]))
    assert len(d.wiki_ops) == 1
    op = d.wiki_ops[0]
    assert op == {
        "op": "retire_page", "id": "stale-page", "card_type": "technique",
        "title": "", "why": "superseded",
    }
    assert "body" not in op and "evidence" not in op


def test_unknown_op_is_dropped():
    d = parse_decision_text(_review_json(wiki_ops=[
        {"op": "delete_everything", "id": "x"},
    ]))
    assert d.wiki_ops == []


def test_create_page_missing_id_is_dropped():
    d = parse_decision_text(_review_json(wiki_ops=[
        {"op": "create_page", "body": "no id here"},
    ]))
    assert d.wiki_ops == []


def test_create_page_missing_body_is_dropped():
    d = parse_decision_text(_review_json(wiki_ops=[
        {"op": "create_page", "id": "no-body"},
    ]))
    assert d.wiki_ops == []


def test_retire_page_missing_id_is_dropped():
    d = parse_decision_text(_review_json(wiki_ops=[
        {"op": "retire_page", "why": "no id"},
    ]))
    assert d.wiki_ops == []


def test_non_dict_entry_is_dropped():
    d = parse_decision_text(_review_json(wiki_ops=["not-a-dict", 42, None]))
    assert d.wiki_ops == []


def test_invalid_card_type_falls_back_to_technique():
    d = parse_decision_text(_review_json(wiki_ops=[{
        "op": "create_page", "id": "x", "card_type": "not-a-real-type",
        "body": "b",
    }]))
    assert d.wiki_ops[0]["card_type"] == "technique"


def test_missing_status_defaults_to_scratch():
    d = parse_decision_text(_review_json(wiki_ops=[{
        "op": "create_page", "id": "x", "body": "b",
    }]))
    assert d.wiki_ops[0]["status"] == "scratch"


def test_malformed_evidence_span_is_dropped_but_op_kept():
    d = parse_decision_text(_review_json(wiki_ops=[{
        "op": "create_page", "id": "x", "body": "b",
        "evidence": ["not-a-dict", {"source_id": "s"}, {"quote": "q"},
                     {"source_id": "s2", "quote": "q2"}],
    }]))
    assert len(d.wiki_ops) == 1
    # Only the one complete span (source_id + quote) survives.
    assert d.wiki_ops[0]["evidence"] == [
        {"source_id": "s2", "quote": "q2", "locator": ""}
    ]


def test_more_than_six_ops_are_truncated():
    ops = [{"op": "retire_page", "id": f"p{i}", "why": "x"} for i in range(10)]
    d = parse_decision_text(_review_json(wiki_ops=ops))
    assert len(d.wiki_ops) == 6


def test_wiki_ops_in_event_payload():
    d = parse_decision_text(_review_json(wiki_ops=[
        {"op": "retire_page", "id": "p", "why": "x"},
    ]))
    payload = d.to_event_payload()
    assert "wiki_ops" in payload
    assert payload["wiki_ops"] == d.wiki_ops
