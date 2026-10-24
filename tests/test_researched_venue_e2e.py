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

from argus.verticals.research.venue_profiles import (
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


def test_venue_language_reviews_describe_claims_without_sentence_templates() -> None:
    root = Path(__file__).parents[1] / "argus/verticals/research/skills/reviewer"
    text = (root / "venue-academic-language-review.md").read_text(encoding="utf-8")
    assert "what is studied, what is claimed, under which conditions" in text
    assert "X is better for Y in Z because W" not in text
    assert "We propose X. We show X improves Y by Z because W" not in text
