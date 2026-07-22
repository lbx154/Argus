"""Closed-loop tests for the shared literary Review contract.

Covers reviewer-text -> extract -> validate -> normalize -> revision_plan ->
assert_plan_covers, plus every rejection the completion definition requires.
`severity` (importance) and `blocking` (gate) are decoupled; no test asserts the
schema merely loads.
"""
from __future__ import annotations

import copy
import json

import pytest

from argus_skill.verticals.literary.shared.review_contract import (
    ReviewError,
    assert_plan_covers,
    blocking_findings,
    extract_review,
    normalize_review,
    revision_plan,
)

_BLOCKING = {
    "id": "f1", "type": "status", "severity": "critical", "blocking": True,
    "location": "第2段「他睁开眼」", "evidence": "c_a.status == dead",
    "suggested_action": "删去复活或补一个解释性的复生事件",
    "must_not_break": ["c_a 仍标记 dead 除非显式复生"],
}
_CRAFT = {
    "id": "f2", "type": "ending", "severity": "note", "blocking": False,
    "location": "结尾", "evidence": "以口号收束", "suggested_action": "改成以景收束",
}


def _review(findings, verdict):
    return {"verdict": verdict, "findings": copy.deepcopy(findings)}


# --- happy paths ----------------------------------------------------------- #
def test_valid_blocking_review_passes():
    r = normalize_review(_review([_BLOCKING], "revise"))
    assert blocking_findings(r)[0]["id"] == "f1"


def test_valid_done_review_with_only_craft_notes():
    r = normalize_review(_review([_CRAFT], "done"))
    assert r["verdict"] == "done"
    assert blocking_findings(r) == []


def test_must_not_break_defaults_to_empty_list():
    f = {k: v for k, v in _CRAFT.items()}  # no must_not_break key
    r = normalize_review(_review([f], "done"))
    assert r["findings"][0]["must_not_break"] == []


# --- severity and blocking are DECOUPLED (the redundancy fix) -------------- #
def test_severity_and_blocking_are_independent():
    # a critical-importance craft note that does NOT gate
    crit_note = {**_CRAFT, "severity": "critical", "blocking": False}
    normalize_review(_review([crit_note], "done"))  # must not raise
    # a blocking gate whose importance is only 'major'
    major_block = {**_BLOCKING, "severity": "major"}
    normalize_review(_review([major_block], "revise"))  # must not raise


# --- missing required finding fields --------------------------------------- #
@pytest.mark.parametrize("missing", ["evidence", "location", "suggested_action",
                                     "type", "severity", "blocking", "id"])
def test_missing_required_finding_field_rejected(missing):
    bad = {k: v for k, v in _BLOCKING.items() if k != missing}
    with pytest.raises(ReviewError):
        normalize_review(_review([bad], "revise"))


# --- malformed / extraction ------------------------------------------------ #
def test_malformed_json_not_silently_accepted():
    with pytest.raises(ReviewError):
        extract_review("here is my review: {verdict: revise, findings: [}")


def test_no_json_object_rejected():
    with pytest.raises(ReviewError):
        extract_review("The chapter looks fine to me, ship it.")


def test_extract_review_happy_from_fenced_text():
    text = "```json\n" + json.dumps(_review([_CRAFT], "done")) + "\n```"
    assert extract_review(text)["verdict"] == "done"


# --- type vocabulary extension policy -------------------------------------- #
def test_unknown_type_rejected_when_vocabulary_supplied():
    f = {**_CRAFT, "type": "made_up_type"}
    with pytest.raises(ReviewError):
        normalize_review(_review([f], "done"), type_vocabulary={"ending", "style"})


def test_unknown_type_accepted_when_no_vocabulary():
    f = {**_CRAFT, "type": "made_up_type"}
    normalize_review(_review([f], "done"))  # no vocabulary -> any non-empty type ok


# --- verdict coherence ----------------------------------------------------- #
def test_blocking_finding_cannot_have_done_verdict():
    with pytest.raises(ReviewError):
        normalize_review(_review([_BLOCKING], "done"))


def test_empty_findings_must_be_done():
    with pytest.raises(ReviewError):
        normalize_review(_review([], "revise"))
    normalize_review(_review([], "done"))  # ok


# --- revision plan + coverage ---------------------------------------------- #
def test_revision_plan_orders_blocking_first_and_carries_must_not_break():
    r = normalize_review(_review([_CRAFT, _BLOCKING], "revise"))
    plan = revision_plan(r)
    assert plan[0]["finding_id"] == "f1"          # blocking first despite listed 2nd
    assert plan[0]["must_not_break"] == ["c_a 仍标记 dead 除非显式复生"]
    assert plan[1]["finding_id"] == "f2"


def test_assert_plan_covers_accepts_a_faithful_plan():
    r = normalize_review(_review([_BLOCKING], "revise"))
    assert_plan_covers(r, revision_plan(r))  # must not raise


def test_assert_plan_covers_rejects_dropped_blocking_finding():
    r = normalize_review(_review([_BLOCKING], "revise"))
    with pytest.raises(ReviewError):
        assert_plan_covers(r, [])


def test_assert_plan_covers_rejects_lost_must_not_break():
    r = normalize_review(_review([_BLOCKING], "revise"))
    weak = [{"finding_id": "f1", "location": "x", "suggested_action": "y",
             "must_not_break": []}]
    with pytest.raises(ReviewError):
        assert_plan_covers(r, weak)


def test_fake_reviewer_dict_drives_deterministic_link():
    plan = revision_plan(normalize_review(_review([_BLOCKING], "revise")))
    assert [p["finding_id"] for p in plan] == ["f1"]


def test_normalize_coerces_integer_finding_id_to_string():
    # Real models routinely emit an integer finding id; normalize must coerce it
    # (a str) rather than reject an otherwise-valid, evidence-bearing review.
    raw = {"verdict": "revise", "findings": [
        {"id": 1, "type": "status", "severity": "critical", "blocking": True,
         "location": "第2段", "evidence": "c_a.status == dead",
         "suggested_action": "删去复活"},
    ]}
    rev = normalize_review(raw)
    assert rev["findings"][0]["id"] == "1"
    assert isinstance(rev["findings"][0]["id"], str)
    # a boolean is NOT an int id — must still fail (bool is not a valid id)
    bad = {"verdict": "revise", "findings": [
        {"id": True, "type": "status", "severity": "critical", "blocking": True,
         "location": "x", "evidence": "y", "suggested_action": "z"},
    ]}
    with pytest.raises(ReviewError):
        normalize_review(bad)
