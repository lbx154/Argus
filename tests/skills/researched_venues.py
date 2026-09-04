"""Sample researched venue profiles for tests.

There are no built-in venues in production code: every profile comes from the
live venue-format research step writing ``research/VENUE_PROFILE.json``. These
fixtures stand in for that researched file — built through
``VenueProfile.from_dict`` so they exercise the same load path — with the two
page geometries that matter for venue-relative behaviour (an 8-page and a
7-page two-column conference) plus a single-column journal with a word limit
instead of a page budget.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.venue_profiles import (
    VenueProfile,
    venue_profile_path,
)

# Two-column conference, 8-page body (Conclusion by p8, References p9+).
EIGHT_PAGE_CONFERENCE = VenueProfile.from_dict(
    {
        "key": "CONFA",
        "display_name": "ConfA 2027",
        "body_page_limit": 8,
        "conclusion_underfill_page": 7,
        "conclusion_max_page": 8,
        "references_min_page": 9,
        "two_column": True,
        "mandatory_end_sections": ["Limitations", "Ethical Considerations"],
        "reviewer_persona": "ConfA",
        "figure_style_persona": "ConfA",
    }
)

# Two-column conference, 7-page body (Conclusion by p7, References p8+).
SEVEN_PAGE_CONFERENCE = VenueProfile.from_dict(
    {
        "key": "CONFB",
        "display_name": "ConfB 2027",
        "body_page_limit": 7,
        "conclusion_underfill_page": 6,
        "conclusion_max_page": 7,
        "references_min_page": 8,
        "two_column": True,
        "requires_reproducibility_checklist": True,
        "reviewer_persona": "ConfB",
        "figure_style_persona": "ConfB",
    }
)

# Single-column journal: no fixed page budget, word-limited main text.
SINGLE_COLUMN_JOURNAL = VenueProfile.from_dict(
    {
        "key": "JOURNALX",
        "display_name": "Journal X",
        "body_page_limit": None,
        "conclusion_underfill_page": None,
        "conclusion_max_page": None,
        "references_min_page": None,
        "two_column": False,
        "main_text_word_limit": 12000,
        "review_model": "single-anonymized",
        "reviewer_persona": "Journal X",
        "figure_style_persona": "Journal X",
    }
)


def seed_researched_profile(
    project_root: Path, profile: VenueProfile = EIGHT_PAGE_CONFERENCE
) -> Path:
    """Write a researched ``research/VENUE_PROFILE.json`` into a test project."""
    path = venue_profile_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.to_dict()), encoding="utf-8")
    return path
