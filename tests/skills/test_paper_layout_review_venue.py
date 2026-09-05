"""Venue-awareness of the deterministic layout page-budget checks.

A layout with Conclusion on page 7 and References on page 8 is *underfilled*
for an 8-page-body venue (references page 9+) but *correct* for a 7-page-body
venue (references page 8+). Conversely Conclusion on page 8 overflows the
7-page body but is fine for the 8-page one. These two cases prove the check
reads the researched venue profile rather than hardcoded page numbers.
"""
from __future__ import annotations

from argus_skill.verticals.research.paper_layout_review import (
    _deterministic_assessment,
    _parse_review_text,
    _vision_prompt,
)
from tests.skills.researched_venues import (
    EIGHT_PAGE_CONFERENCE,
    SEVEN_PAGE_CONFERENCE,
    SINGLE_COLUMN_JOURNAL,
)


def _layout(pages: list[str]) -> str:
    return "\f".join(pages)


def _codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def _assess(layout: str, venue) -> dict:
    return _deterministic_assessment(
        tex_text="", log_text="", layout_text=layout, threshold=3.5, venue=venue
    )


def test_conclusion_p7_references_p8_fits_seven_page_body_not_eight() -> None:
    layout = _layout(
        [f"body {i}" for i in range(1, 7)] + ["Conclusion", "References\n[1] foo"]
    )
    eight = _codes(_assess(layout, EIGHT_PAGE_CONFERENCE))
    seven = _codes(_assess(layout, SEVEN_PAGE_CONFERENCE))
    # 8-page venue: references start before page 9 -> underfilled body.
    assert "references_before_full_body" in eight
    # 7-page venue: references on page 8 is exactly right -> no page-budget issue.
    assert "references_before_full_body" not in seven
    assert "rendered_main_body_underfilled" not in seven
    assert "conclusion_after_page_8" not in seven


def test_conclusion_p8_references_p9_fits_eight_page_body_overflows_seven() -> None:
    layout = _layout(
        [f"body {i}" for i in range(1, 8)] + ["Conclusion", "References\n[1] foo"]
    )
    eight = _codes(_assess(layout, EIGHT_PAGE_CONFERENCE))
    seven = _codes(_assess(layout, SEVEN_PAGE_CONFERENCE))
    # 8-page venue: conclusion by page 8, references page 9 -> clean budget.
    assert "conclusion_after_page_8" not in eight
    assert "references_before_full_body" not in eight
    # 7-page venue: conclusion on page 8 exceeds the 7-page body.
    assert "conclusion_after_page_8" in seven


def test_page_flow_contract_values_are_venue_relative() -> None:
    layout = _layout(
        [f"body {i}" for i in range(1, 7)] + ["Conclusion", "References\n[1] foo"]
    )
    eight_pfc = _assess(layout, EIGHT_PAGE_CONFERENCE)["page_flow_contract"]
    seven_pfc = _assess(layout, SEVEN_PAGE_CONFERENCE)["page_flow_contract"]
    # Same references page (8), opposite verdicts driven purely by the profile.
    assert eight_pfc["references_after_body"] is False
    assert seven_pfc["references_after_body"] is True
    # The contract carries the researched budget, never a fixed page number.
    assert eight_pfc["fixed_page_budget_enforced"] is True
    assert eight_pfc["post_body_pages_uncapped"] is True


def test_figure_review_uses_good_enough_non_looping_standard() -> None:
    prompt = _vision_prompt(
        deterministic={},
        threshold=3.5,
        venue=EIGHT_PAGE_CONFERENCE,
    )

    assert "good-looking-enough" in prompt
    assert "at most one targeted aesthetic repair" in prompt
    assert "Optional renderer metadata may help" in prompt
    assert "Wrong, reversed, missing, or unsupported arrows" in prompt
    assert "connector penetration" in prompt
    assert "overlapping nodes or text" in prompt
    assert "unreadable final-size typography" in prompt
    assert "not cosmetic preferences" in prompt
    assert "Write a prose review, not JSON" in prompt
    assert "score_1_to_5" not in prompt
    assert "criteria_scores" not in prompt


def test_layout_review_never_demands_padding_to_fill_the_budget() -> None:
    # The page budget is a ceiling, not a quota: the reviewer prompt must not
    # ask authors to pad or lengthen a paper, and an early References page is
    # not by itself a defect.
    prompt = _vision_prompt(
        deterministic={},
        threshold=3.5,
        venue=EIGHT_PAGE_CONFERENCE,
    )
    assert "ceiling, not a quota" in prompt
    assert "never ask the author to pad" in prompt
    assert "should be expanded" not in prompt


def test_layout_review_preserves_unstructured_prose() -> None:
    raw = "Blocking — page 3, Table 2 overlaps text. Split the table and recompile."

    assert _parse_review_text(raw) == {"review_text": raw}


def test_top_level_heading_detection_ignores_abstract_conclusion_label() -> None:
    layout = _layout(
        [
            "ABSTRACT\nBackground: x. Conclusion: this is an abstract label.",
            "body",
            "body",
            "body",
            "body",
            "body",
            "body",
            "  224   10. CONCLUSION  ",
            "  9. REFERENCES  \nSmith (2024)",
            "more references",
        ]
    )
    page_flow = _assess(layout, SINGLE_COLUMN_JOURNAL)["page_flow_contract"]
    assert page_flow["conclusion_page"] == 8
    assert page_flow["references_page"] == 9
    assert page_flow["fixed_page_budget_enforced"] is False


def test_word_limit_journal_has_no_fixed_page_underfill_or_overflow_check() -> None:
    result = _assess(
        _layout(["CONCLUSION", "REFERENCES\nSmith (2024)"]),
        SINGLE_COLUMN_JOURNAL,
    )
    codes = _codes(result)
    assert "rendered_main_body_underfilled" not in codes
    assert "conclusion_after_page_8" not in codes
    assert "references_before_full_body" not in codes
    assert "references_share_body_page" not in codes


def test_two_column_heading_cells_are_detected() -> None:
    layout = _layout(
        [
            "left-column prose          8 Conclusion",
            "left-column prose          9 REFERENCES",
        ]
    )
    page_flow = _assess(layout, EIGHT_PAGE_CONFERENCE)["page_flow_contract"]
    assert page_flow["conclusion_page"] == 1
    assert page_flow["references_page"] == 2
