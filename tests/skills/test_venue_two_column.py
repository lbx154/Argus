"""A researched venue profile says whether the venue is two-column.

Venue facts come from researched profiles; ``two_column`` defaults to True when
a profile omits it and survives a JSON round trip.
"""
from __future__ import annotations

import json

from argus.verticals.research.venue_profiles import VenueProfile
from tests.skills.researched_venues import (
    EIGHT_PAGE_CONFERENCE,
    SEVEN_PAGE_CONFERENCE,
)


def _single_col_venue() -> VenueProfile:
    return VenueProfile(
        key="NEURIPS",
        display_name="NeurIPS 2026",
        body_page_limit=9,
        conclusion_underfill_page=8,
        conclusion_max_page=9,
        references_min_page=10,
        two_column=False,
    )


def test_two_column_defaults_true_and_round_trips() -> None:
    assert EIGHT_PAGE_CONFERENCE.two_column is True
    assert SEVEN_PAGE_CONFERENCE.two_column is True
    sc = _single_col_venue()
    assert sc.two_column is False
    # from_dict default when the researched profile omits the field.
    minimal = VenueProfile.from_dict(
        {
            "key": "K",
            "display_name": "K",
            "body_page_limit": None,
            "conclusion_underfill_page": None,
            "conclusion_max_page": None,
            "references_min_page": None,
        }
    )
    assert minimal.two_column is True
    rt = VenueProfile.from_dict(json.loads(json.dumps(sc.to_dict())))
    assert rt == sc and rt.two_column is False
