"""Venue-awareness of the deterministic layout page-budget gate.

A layout with Conclusion on page 7 and References on page 8 is *underfilled*
for EMNLP (8-page body, references page 9+) but *correct* for AAAI (7-page
body, references page 8+). Conversely Conclusion on page 8 overflows AAAI's
7-page body but is fine for EMNLP. These two cases prove the gate reads the
venue profile rather than hardcoded ACL page numbers.
"""
from __future__ import annotations

from argus_skill.skills.paper_layout_review import _deterministic_assessment
from argus_skill.skills.venue_profiles import AAAI_PROFILE, EMNLP_PROFILE


def _layout(pages: list[str]) -> str:
    return "\f".join(pages)


def _codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


def _assess(layout: str, venue) -> dict:
    return _deterministic_assessment(
        tex_text="", log_text="", layout_text=layout, threshold=3.5, venue=venue
    )


def test_conclusion_p7_references_p8_passes_aaai_fails_emnlp() -> None:
    layout = _layout(
        [f"body {i}" for i in range(1, 7)] + ["Conclusion here", "References\n[1] foo"]
    )
    emnlp = _codes(_assess(layout, EMNLP_PROFILE))
    aaai = _codes(_assess(layout, AAAI_PROFILE))
    # EMNLP: references start before page 9 -> underfilled body.
    assert "references_before_full_body" in emnlp
    # AAAI: references on page 8 is exactly right -> no page-budget issue.
    assert "references_before_full_body" not in aaai
    assert "rendered_main_body_underfilled" not in aaai
    assert "conclusion_after_page_8" not in aaai


def test_conclusion_p8_references_p9_passes_emnlp_overflows_aaai() -> None:
    layout = _layout(
        [f"body {i}" for i in range(1, 8)] + ["Conclusion here", "References\n[1] foo"]
    )
    emnlp = _codes(_assess(layout, EMNLP_PROFILE))
    aaai = _codes(_assess(layout, AAAI_PROFILE))
    # EMNLP: conclusion by page 8, references page 9 -> clean budget.
    assert "conclusion_after_page_8" not in emnlp
    assert "references_before_full_body" not in emnlp
    # AAAI: conclusion on page 8 exceeds the 7-page body.
    assert "conclusion_after_page_8" in aaai


def test_page_flow_contract_values_are_venue_relative() -> None:
    layout = _layout(
        [f"body {i}" for i in range(1, 7)] + ["Conclusion here", "References\n[1] foo"]
    )
    emnlp_pfc = _assess(layout, EMNLP_PROFILE)["page_flow_contract"]
    aaai_pfc = _assess(layout, AAAI_PROFILE)["page_flow_contract"]
    # Same references page (8), opposite verdicts driven purely by the profile.
    assert emnlp_pfc["references_on_or_after_page_9"] is False
    assert aaai_pfc["references_on_or_after_page_9"] is True
