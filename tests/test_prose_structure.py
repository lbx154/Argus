"""prose STRUCTURE checks — the honest, thin deterministic layer.

Machine-checks only prose_state completeness and declared hard constraints
(language/paragraph-count/banned-words). Each has a real negative. Fact/memory,
observation, and movement are NOT here — they are live-reviewer.
"""
from __future__ import annotations

from argus_skill.verticals.prose.structure import (
    PROSE_STATE_FIELDS,
    STRUCTURE_FINDING_TYPES,
    check_draft,
    is_compliant,
    validate_prose_state,
)

_STATE = {
    "narrative_center": "祖母的厨房",
    "observation_subject": "灶台与光",
    "factual_anchors": ["1998年", "老屋"],
    "memory_boundary": "厨房的气味是回忆，年份是事实",
    "paragraph_movement": "从物到人到时间",
    "ending_strategy": "以一个动作收束，不升华",
}
_DRAFT = "灶台还在那里。\n\n光从窗格里斜下来，落在她的手背上。\n\n后来老屋拆了。"


def test_complete_state_and_clean_draft_pass():
    assert validate_prose_state(_STATE) == []
    assert is_compliant(_DRAFT, {"language": "zh"})


def test_missing_prose_state_field_flagged():
    bad = dict(_STATE)
    del bad["memory_boundary"]
    fs = validate_prose_state(bad)
    assert any(f["type"] == "structure" and "memory_boundary" in f["detail"] for f in fs)


def test_empty_prose_state_field_flagged():
    bad = dict(_STATE)
    bad["factual_anchors"] = []
    fs = validate_prose_state(bad)
    assert any(f["type"] == "structure" for f in fs)


def test_paragraph_count_bounds():
    assert any(f["type"] == "paragraph_count" for f in check_draft(_DRAFT, {"min_paragraphs": 5}))
    assert any(f["type"] == "paragraph_count" for f in check_draft(_DRAFT, {"max_paragraphs": 2}))
    assert is_compliant(_DRAFT, {"min_paragraphs": 3, "max_paragraphs": 3})


def test_banned_and_language():
    assert any(f["type"] == "banned_word" for f in check_draft(_DRAFT, {"banned_words": ["光"]}))
    assert any(f["type"] == "language" for f in check_draft(_DRAFT, {"language": "en"}))


def test_empty_draft_flagged():
    assert any(f["type"] == "empty" for f in check_draft("   \n\n  ", {}))


def test_vocabulary_and_fields():
    assert STRUCTURE_FINDING_TYPES == {"structure", "language", "paragraph_count", "banned_word", "empty"}
    assert "memory_boundary" in PROSE_STATE_FIELDS and "narrative_center" in PROSE_STATE_FIELDS
