"""End-to-end venue path: researched profile resolution and unified final Review.

Venue format facts come only from the researched ``research/VENUE_PROFILE.json``
(written by the live venue-format research step from the venue's official
author kit). Venue-specific format checks remain in the Paper skills while
scientific, visual, and language acceptance share the final Review stage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_machine import format_stage_checklist
from argus_skill.verticals.research.paper_layout_review import _deterministic_assessment
from argus_skill.verticals.research.venue_profiles import (
    resolve_venue_profile,
    venue_profile_path,
)

pytestmark = pytest.mark.e2e

_RESEARCHED_PROFILE = {
    "key": "AAAI",
    "display_name": "AAAI 2027",
    "body_page_limit": 7,
    "conclusion_underfill_page": 6,
    "conclusion_max_page": 7,
    "references_min_page": 8,
    "two_column": True,
    "reviewer_persona": "AAAI",
    "figure_style_persona": "AAAI",
}


def _seed_project(tmp_path: Path, target_venue: str = "aaai2027") -> Path:
    """Seed the state a real project has after venue-format research ran:
    ``target_venue`` in the pipeline state plus the researched profile the
    research step wrote from the official author kit."""
    state = {
        "current_stage": "research",
        "vertical": "research",
        "objective": "obj",
        "target_venue": target_venue,
    }
    target = tmp_path / ".argus" / "PIPELINE_STATE.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    profile = venue_profile_path(tmp_path)
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps(_RESEARCHED_PROFILE), encoding="utf-8")
    return tmp_path


def test_researched_profile_resolves_for_the_selected_venue(tmp_path: Path) -> None:
    root = _seed_project(tmp_path, target_venue="AAAI 2027")
    venue = resolve_venue_profile(root)
    assert venue.key == "AAAI"
    assert venue.has_fixed_page_budget is True


def test_layout_review_uses_the_researched_page_budget(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    venue = resolve_venue_profile(root)
    # Conclusion p7 / References p8: exactly the researched budget.
    layout = "\f".join(
        [f"body {i}" for i in range(1, 7)] + ["Conclusion x", "References\n[1] a"]
    )
    codes = {
        i["code"]
        for i in _deterministic_assessment(
            tex_text="", log_text="", layout_text=layout, threshold=3.5, venue=venue
        )["issues"]
    }
    assert "references_before_full_body" not in codes


def test_review_checklist_and_reviewer_skill_are_venue_neutral(tmp_path: Path) -> None:
    root = _seed_project(tmp_path)
    review = format_stage_checklist("review", role="reviewer", project_root=root)
    assert "review.visual" in review
    assert "review.language" in review
    profile = resolve_venue_profile(root)
    assert profile.review_skill_path == "reviewer/venue-academic-language-review.md"
    format_skill = (
        Path(__file__).parents[1]
        / "argus_skill/verticals/research/skills/engineer/venue-format-preflight.md"
    ).read_text(encoding="utf-8")
    assert "selected venue" in format_skill
    assert "official author kit" in format_skill


def test_unresearched_venue_fails_closed(tmp_path: Path) -> None:
    # A named venue with no researched profile must not silently borrow another
    # venue's rules; resolution points at the venue-format research step.
    state = {
        "current_stage": "research",
        "vertical": "research",
        "objective": "obj",
        "target_venue": "NeurIPS",
    }
    target = tmp_path / ".argus" / "PIPELINE_STATE.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(KeyError, match="researched"):
        resolve_venue_profile(tmp_path)


def test_venue_language_reviews_describe_claims_without_sentence_templates() -> None:
    root = Path(__file__).parents[1] / "argus_skill/verticals/research/skills/reviewer"
    text = (root / "venue-academic-language-review.md").read_text(encoding="utf-8")
    assert "what is studied, what is claimed, under which conditions" in text
    assert "X is better for Y in Z because W" not in text
    assert "We propose X. We show X improves Y by Z because W" not in text
