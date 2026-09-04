"""Venue profiles are researched, never built in.

Every venue's format facts come from the live venue-format research step,
which writes ``research/VENUE_PROFILE.json`` from the venue's official author
kit. These tests cover loading that researched profile, resolving it for a
project, and failing closed when no researched profile exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.verticals.research.venue_profiles import (
    VenueProfile,
    load_local_venue_profile,
    resolve_venue_profile,
    venue_profile_path,
)

_RESEARCHED_CONFERENCE = {
    "key": "AAAI",
    "display_name": "AAAI 2027",
    "body_page_limit": 7,
    "conclusion_underfill_page": 6,
    "conclusion_max_page": 7,
    "references_min_page": 8,
    "two_column": True,
    "style_package": "aaai2027",
    "emit_bibliographystyle": False,
    "requires_pdfinfo": True,
    "forbidden_packages": ["hyperref", "navigator"],
    "reviewer_persona": "AAAI",
    "figure_style_persona": "AAAI",
}

_RESEARCHED_JOURNAL = {
    "key": "FRONTIERSAI",
    "display_name": "Frontiers in Artificial Intelligence",
    "body_page_limit": None,
    "conclusion_underfill_page": None,
    "conclusion_max_page": None,
    "references_min_page": None,
    "two_column": False,
    "main_text_word_limit": 12000,
    "review_model": "single-anonymized",
}


def _write_state(root: Path, payload: dict) -> None:
    (root / ".argus").mkdir(parents=True, exist_ok=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_profile(root: Path, payload: dict) -> Path:
    path = venue_profile_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_no_builtin_venue_profiles_exist() -> None:
    import argus_skill.verticals.research.venue_profiles as vp

    # No registry of shipped venues, no get-by-key lookup: the only way to a
    # profile is the researched project-local file.
    assert not hasattr(vp, "get_venue_profile")
    assert not hasattr(vp, "EMNLP_PROFILE")
    assert not hasattr(vp, "AAAI_PROFILE")
    for name, value in vars(vp).items():
        assert not isinstance(value, VenueProfile), name


def test_from_dict_round_trip_and_defaults() -> None:
    p = VenueProfile.from_dict(_RESEARCHED_CONFERENCE)
    assert p.key == "AAAI"
    assert p.has_fixed_page_budget is True
    assert (p.conclusion_underfill_page, p.conclusion_max_page, p.references_min_page) == (6, 7, 8)
    assert p.emit_bibliographystyle is False
    assert p.forbidden_packages == ("hyperref", "navigator")
    # Unspecified optional fields keep their dataclass defaults.
    assert p.review_skill_path == "reviewer/venue-academic-language-review.md"
    assert p.academic_language_rubric_id == "venue-academic-language-v1"
    # Round trip through to_dict.
    again = VenueProfile.from_dict(p.to_dict())
    assert again == p


def test_from_dict_journal_profile_without_page_budget() -> None:
    p = VenueProfile.from_dict(_RESEARCHED_JOURNAL)
    assert p.has_fixed_page_budget is False
    assert p.two_column is False
    assert p.main_text_word_limit == 12000
    assert "12,000" in p.page_budget_line()


def test_from_dict_rejects_bad_payloads() -> None:
    with pytest.raises(ValueError):
        VenueProfile.from_dict("not a dict")
    with pytest.raises(ValueError):
        VenueProfile.from_dict({"key": "X"})  # missing required geometry fields


def test_resolve_uses_researched_local_profile(tmp_path: Path) -> None:
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "AAAI"})
    _write_profile(tmp_path, _RESEARCHED_CONFERENCE)
    assert resolve_venue_profile(tmp_path).key == "AAAI"
    # The documented validation command passes a string project root.
    assert resolve_venue_profile(str(tmp_path)).key == "AAAI"


def test_resolve_matches_venue_key_variants(tmp_path: Path) -> None:
    # A planner naturally writes "aaai2027" / "AAAI 2027" / "AAAI-27"; all must
    # match the researched profile rather than trigger a fresh research pass.
    _write_profile(tmp_path, _RESEARCHED_CONFERENCE)
    for token in ("AAAI", "aaai", "aaai2027", "AAAI 2027", "AAAI-27", "aaai-2027"):
        _write_state(tmp_path, {"current_stage": "plan", "target_venue": token})
        assert resolve_venue_profile(tmp_path).key == "AAAI", token


def test_resolve_fails_closed_without_researched_profile(tmp_path: Path) -> None:
    # No profile and no target venue: fail closed, never guess a venue.
    _write_state(tmp_path, {"current_stage": "plan"})
    with pytest.raises(KeyError):
        resolve_venue_profile(tmp_path)
    with pytest.raises(KeyError):
        resolve_venue_profile(tmp_path / "nonexistent")
    # A named venue with no researched profile also fails closed, pointing at
    # the venue-format research step.
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "NeurIPS"})
    with pytest.raises(KeyError, match="researched"):
        resolve_venue_profile(tmp_path)


def test_resolve_rejects_profile_for_a_different_venue(tmp_path: Path) -> None:
    # A stale profile for another venue must not silently grade this paper.
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "NeurIPS"})
    _write_profile(tmp_path, _RESEARCHED_CONFERENCE)
    with pytest.raises(KeyError):
        resolve_venue_profile(tmp_path)


def test_local_profile_load_is_fail_soft(tmp_path: Path) -> None:
    assert load_local_venue_profile(tmp_path) is None
    path = _write_profile(tmp_path, _RESEARCHED_CONFERENCE)
    loaded = load_local_venue_profile(tmp_path)
    assert loaded is not None and loaded.key == "AAAI"
    path.write_text("not json", encoding="utf-8")
    assert load_local_venue_profile(tmp_path) is None
