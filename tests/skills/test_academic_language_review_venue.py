"""Venue-awareness of the academic-language model-review prompt.

The audit found the model prompt was venue-blind (hardcoded "EMNLP long paper",
"ACL/EMNLP standard: abstracts under 170 words", "eight-page body budget") even
on AAAI runs. EMNLP must stay byte-identical; AAAI must get its persona, the
advisory (no-hard-floor) abstract policy, and the 7-page body budget.
"""
from __future__ import annotations

from argus_skill.skills.academic_language_review import _review_prompt
from argus_skill.skills.venue_profiles import AAAI_PROFILE, EMNLP_PROFILE

_SRC = {"paper/main.tex": "x"}
_DET = {"k": 1}


def _prompt(venue) -> str:
    return _review_prompt(
        source_text_by_path=_SRC, deterministic=_DET, threshold=4.0, venue=venue
    )


def test_emnlp_prompt_keeps_emnlp_standard() -> None:
    p = _prompt(EMNLP_PROFILE)
    assert "final academic-language reviewer for an EMNLP long paper" in p
    assert "Apply this ACL/EMNLP standard: abstracts under 170 words are too thin" in p
    assert "eight-page body budget" in p


def test_aaai_prompt_is_venue_correct() -> None:
    p = _prompt(AAAI_PROFILE)
    assert "AAAI paper" in p
    assert "EMNLP long paper" not in p
    # AAAI has no official abstract word limit -> advisory wording, no 170 floor.
    assert "no official abstract word limit" in p
    assert "abstracts under 170 words are too thin" not in p
    assert "ACL/EMNLP standard" not in p
    # AAAI 7-page body budget, not the ACL eight-page one.
    assert "7-page body budget" in p
    assert "eight-page body budget" not in p
