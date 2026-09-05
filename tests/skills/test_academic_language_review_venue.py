"""Venue-awareness of the academic-language model-review prompt.

Every venue-local string derives from the researched profile, while the shared
abstract guidance remains venue-independent.
"""
from __future__ import annotations

from argus_skill.verticals.research.academic_language_review import (
    _parse_review_text,
    _review_prompt,
)
from tests.skills.researched_venues import (
    EIGHT_PAGE_CONFERENCE,
    SEVEN_PAGE_CONFERENCE,
    SINGLE_COLUMN_JOURNAL,
)

_SRC = {"paper/main.tex": "x"}
_DET = {"k": 1}


def _prompt(venue) -> str:
    return _review_prompt(
        source_text_by_path=_SRC, deterministic=_DET, venue=venue
    )


def test_prompt_persona_and_budget_come_from_the_researched_profile() -> None:
    eight = _prompt(EIGHT_PAGE_CONFERENCE)
    assert "reviewer for a paper submitted to ConfA 2027" in eight
    assert "8-page body budget" in eight

    seven = _prompt(SEVEN_PAGE_CONFERENCE)
    assert "a paper submitted to ConfB 2027" in seven
    assert "7-page body budget" in seven
    assert "8-page body budget" not in seven
    assert "ConfA" not in seven


def test_prompt_has_no_hardcoded_venue_names_and_keeps_abstract_contract() -> None:
    for venue in (EIGHT_PAGE_CONFERENCE, SEVEN_PAGE_CONFERENCE, SINGLE_COLUMN_JOURNAL):
        p = _prompt(venue)
        for literal in ("EMNLP", "AAAI", "ACL", "NeurIPS"):
            assert literal not in p
        assert "flat experiment checklist" in p


def test_word_limit_journal_prompt_uses_word_budget_phrasing() -> None:
    p = _prompt(SINGLE_COLUMN_JOURNAL)
    assert "12,000" in p
    assert "-page body budget" not in p


def test_language_prompt_requests_prose_without_schema_ceremony() -> None:
    prompt = _prompt(EIGHT_PAGE_CONFERENCE)

    assert "Write a prose review, not JSON" in prompt
    assert "score_1_to_5" not in prompt
    assert "section_scores object" not in prompt
    assert "revision_directives list" not in prompt


def test_language_review_preserves_unstructured_prose() -> None:
    raw = "Major — Introduction, line 12: the claim is unsupported. Cite the result."

    assert _parse_review_text(raw) == {"review_text": raw}
