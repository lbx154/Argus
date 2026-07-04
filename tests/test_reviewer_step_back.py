"""Tests for the Reviewer → Planner STEP-BACK reflection channel (anti-plan-lock-in).

The reviewer authors ``step_back`` on EVERY round that produced a measured result —
INCLUDING a clean success — so the planner is forced (rule 17d) to consider new
questions / alternative directions even when the plan appears to be working. This is
the mechanism that stops the pipeline marching forward along the initial plan without
ever asking the questions a fresh human would ask.

Mirrors test_reviewer_checklist_feedback.py + test_planner_report_evidence_files.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.core.models import ReviewDecision
from argus_skill.life.supervisor import LifeSupervisor
from argus_skill.reviewer import SCHEMA_PATH, _parse_step_back
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
        "step_back": None,
    }
    base.update(over)
    return json.dumps(base)


_GOOD_SB = {
    "supported_by_results": "partial",
    "surprises": "the gap is much larger on the harder split than the headline implies",
    "new_questions": [
        "is the improvement from capability or from prompt formatting?",
        "does it hold on the out-of-distribution split?",
    ],
    "alt_directions": [
        {"direction": "run a chain-of-thought prompt variant", "why": "isolate formatting from capability", "cheap_to_test": True},
        {"direction": "add a 200-row OOD eval slice", "why": "test generalization", "cheap_to_test": True},
    ],
}


# --- parsing -----------------------------------------------------------------

def test_absent_step_back_is_none():
    d = parse_decision_text(_review_json())
    assert d is not None and d.step_back is None


def test_step_back_parsed_full():
    d = parse_decision_text(_review_json(step_back=_GOOD_SB))
    assert d is not None
    assert d.step_back is not None
    assert d.step_back["supported_by_results"] == "partial"
    assert len(d.step_back["new_questions"]) == 2
    assert d.step_back["alt_directions"][0]["cheap_to_test"] is True


def test_step_back_survives_on_clean_success():
    # THE core regression for the operator's complaint: a success-side reflection
    # (status=done, forward_progress=true) must NOT be dropped.
    d = parse_decision_text(_review_json(
        status="done",
        planner_report={"forward_progress": True, "headline": "h", "blocker": "",
                        "recommended_next": "", "evidence_files": []},
        step_back=_GOOD_SB,
    ))
    assert d is not None
    assert d.step_back is not None, "step_back dropped on a clean success!"
    assert d.step_back["surprises"]


def test_invalid_supported_by_results_normalizes_blank():
    d = parse_decision_text(_review_json(step_back={**_GOOD_SB, "supported_by_results": "maybe"}))
    assert d.step_back is not None
    assert d.step_back["supported_by_results"] == ""


def test_alt_direction_missing_direction_is_dropped_fail_soft():
    sb = {**_GOOD_SB, "alt_directions": [
        {"why": "no direction key", "cheap_to_test": True},          # dropped
        {"direction": "keep me", "why": "ok", "cheap_to_test": False},  # kept
    ]}
    d = parse_decision_text(_review_json(step_back=sb))
    assert d.step_back is not None
    assert len(d.step_back["alt_directions"]) == 1
    assert d.step_back["alt_directions"][0]["direction"] == "keep me"
    assert d.step_back["alt_directions"][0]["cheap_to_test"] is False


def test_step_back_caps_and_trims():
    sb = {
        "supported_by_results": "no",
        "surprises": "z" * 5000,
        "new_questions": [f"q{i}" for i in range(20)],
        "alt_directions": [{"direction": f"d{i}", "why": "w", "cheap_to_test": True} for i in range(20)],
    }
    out = _parse_step_back({"step_back": sb})
    assert out is not None
    assert len(out["surprises"]) <= 1200
    assert len(out["new_questions"]) <= 5
    assert len(out["alt_directions"]) <= 4


def test_empty_step_back_object_is_none():
    # An all-empty object carries no signal → None (planner sees nothing to triage).
    assert _parse_step_back({"step_back": {"supported_by_results": "", "surprises": "",
                                           "new_questions": [], "alt_directions": []}}) is None


def test_step_back_not_a_dict_is_none():
    assert _parse_step_back({"step_back": "oops"}) is None
    assert _parse_step_back({}) is None


# --- event payload (postmortem visibility) -----------------------------------

def test_step_back_in_event_payload():
    d = parse_decision_text(_review_json(step_back=_GOOD_SB))
    payload = d.to_event_payload()
    assert "step_back" in payload
    assert payload["step_back"]["supported_by_results"] == "partial"


def test_step_back_payload_none_when_absent():
    d = parse_decision_text(_review_json())
    assert d.to_event_payload()["step_back"] is None


# --- supervisor render (the planner actually sees it) ------------------------

def test_render_step_back_shows_questions_and_directions():
    rendered = LifeSupervisor._render_step_back(_GOOD_SB)
    assert "STEP_BACK" in rendered
    assert "17d" in rendered              # tells the planner it MUST triage
    assert "supported_by_results: partial" in rendered
    assert "new_questions" in rendered
    assert "capability or from prompt formatting" in rendered
    assert "alt_directions" in rendered
    assert "chain-of-thought" in rendered
    assert "[cheap_to_test]" in rendered  # cheap probes are flagged for the planner


def test_render_step_back_empty_returns_blank():
    assert LifeSupervisor._render_step_back({}) == ""
    assert LifeSupervisor._render_step_back(
        {"supported_by_results": "", "surprises": "", "new_questions": [], "alt_directions": []}
    ) == ""


# --- schema strictness (codex --output-schema surrogate) ---------------------

def test_schema_has_step_back_in_properties_and_required():
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    assert "step_back" in schema["properties"]
    assert "step_back" in schema["required"]


def test_schema_step_back_nested_required_complete():
    # The 2026-06-26 outage was an under-specified nested `required`; pin ours.
    schema = json.loads(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    sb = schema["properties"]["step_back"]
    assert set(sb["required"]) == set(sb["properties"]) == {
        "supported_by_results", "surprises", "new_questions", "alt_directions",
    }
    alt_items = sb["properties"]["alt_directions"]["items"]
    assert set(alt_items["required"]) == set(alt_items["properties"]) == {
        "direction", "why", "cheap_to_test",
    }
    assert sb["additionalProperties"] is False
    assert alt_items["additionalProperties"] is False


# --- planner is wired to triage it (rule 17d) --------------------------------

def test_planner_preamble_has_step_back_triage_rule():
    from argus_skill.planner.planner import _PLANNER_SYSTEM_PREAMBLE as preamble
    assert "17d)" in preamble
    assert "STEP_BACK" in preamble or "STEP-BACK" in preamble
    assert "alt_direction" in preamble
    # The anti-lock-in framing + the success-side fire are the load-bearing parts.
    assert "locked into its initial plan" in preamble


# --- ReviewDecision default ---------------------------------------------------

def test_review_decision_step_back_defaults_none():
    d = ReviewDecision(status="continue", reason="r", next_action="n")
    assert d.step_back is None
