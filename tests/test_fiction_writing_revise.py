"""Consumer-side closed-loop tests: fiction revise consumes the Review contract.

Proves the shared review payload is consumed by fiction's revise adapter against
fiction's own finding vocabulary; the runtime STAGE_CHECK wiring is covered
separately in test_fiction_writing_revise_runtime.py.
"""
from __future__ import annotations

import json

import pytest

from argus_skill.literary.review_contract import ReviewError
from argus_skill.verticals.fiction_writing.revise import (
    FICTION_FINDING_TYPES,
    fiction_revision_plan,
    fiction_revision_plan_from_text,
)


def _review(findings, verdict="revise"):
    return {"verdict": verdict, "findings": findings}


def test_fiction_continuity_finding_becomes_blocking_first_instruction():
    review = _review([
        {"id": "c1", "type": "knowledge", "severity": "critical", "blocking": True,
         "location": "第3段", "evidence": "凶手身份不在 c_b.knows",
         "suggested_action": "让 c_b 先在场目击，或改为推测语气",
         "must_not_break": ["c_b 的知情边界"]},
        {"id": "c2", "type": "pacing", "severity": "minor", "blocking": False,
         "location": "第1段", "evidence": "开场偏慢",
         "suggested_action": "前移钩子"},
    ])
    plan = fiction_revision_plan(review)
    assert plan[0]["finding_id"] == "c1"
    assert plan[0]["blocking"] is True
    assert plan[0]["must_not_break"] == ["c_b 的知情边界"]


def test_fiction_rejects_finding_type_outside_its_vocabulary():
    review = _review([
        {"id": "x", "type": "prosody_pingze", "severity": "critical", "blocking": True,
         "location": "l1", "evidence": "平仄失替",
         "suggested_action": "调整"},  # a poetry type — not fiction's
    ])
    with pytest.raises(ReviewError):
        fiction_revision_plan(review)


def test_fiction_parses_reviewer_text_output():
    text = "verdict below\n" + json.dumps(_review([
        {"id": "c1", "type": "language", "severity": "critical", "blocking": True,
         "location": "全篇", "evidence": "brief 要求 en，草稿是 zh",
         "suggested_action": "改回英文"},
    ]))
    plan = fiction_revision_plan_from_text(text)
    assert plan[0]["type"] == "language"


def test_all_fiction_continuity_types_are_valid_vocabulary():
    for t in ("status", "knowledge", "item_location", "co_location", "timeline",
              "world_rule", "motivation", "foreshadowing", "viewpoint", "language",
              "temporal_consistency"):
        assert t in FICTION_FINDING_TYPES


def test_blocking_voice_finding_passes_the_contract():
    # the style lint may raise a craft-vocabulary type (voice/ai_tell) to
    # blocking=True on an author-declared hard contract; that must validate and
    # order first, exactly like a continuity finding.
    review = _review([
        {"id": "v1", "type": "voice", "severity": "major", "blocking": True,
         "location": "第2段", "evidence": "forbidden term '手机' present",
         "suggested_action": "改为符合语域的说法"},
    ])
    plan = fiction_revision_plan(review)
    assert plan[0]["finding_id"] == "v1"
    assert plan[0]["blocking"] is True

