"""Tests for the Reviewer → Planner checklist feedback channel.

The Reviewer is FEEDBACK-ONLY: it parses ``checklist_feedback`` into the verdict
and NEVER writes the checklist store.
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
        "checklist_feedback": None,
    }
    base.update(over)
    return json.dumps(base)


def test_absent_feedback_is_none():
    d = parse_decision_text(_review_json())
    assert d is not None and d.checklist_feedback is None


def test_feedback_parsed():
    d = parse_decision_text(_review_json(checklist_feedback={
        "stage": "simulate",
        "summary": "item too strict for a single-seed task",
        "items": [{"id": "simulate.seeds", "problem": "requires 10 seeds",
                   "suggested_fix": "modify: >=3"}],
    }))
    assert d.checklist_feedback["stage"] == "simulate"
    assert d.checklist_feedback["items"][0]["id"] == "simulate.seeds"


def test_feedback_in_event_payload():
    d = parse_decision_text(_review_json(checklist_feedback={
        "stage": "simulate", "summary": "x", "items": [],
    }))
    assert "checklist_feedback" in d.to_event_payload()


def test_reviewer_never_writes_checklist_store(tmp_path, monkeypatch):
    # Parsing a verdict carrying checklist_feedback must NOT create the store file.
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))
    parse_decision_text(_review_json(checklist_feedback={
        "stage": "simulate", "summary": "x",
        "items": [{"id": "a", "problem": "p", "suggested_fix": "f"}],
    }))
    assert not (tmp_path / "research" / "CHECKLISTS.json").exists()
