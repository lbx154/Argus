"""Venue registry in research_profile.load_research_profile."""
from __future__ import annotations

from argus_skill.life.research_profile import load_research_profile


def test_emnlp_profile_unchanged() -> None:
    p = load_research_profile({"ARGUS_SKILL_RESEARCH_PROFILE": "emnlp2026-tierharness"})
    assert p is not None
    assert p.name == "emnlp2026-tierharness"
    assert "EMNLP 2026 TierHarness project" in p.text
    # EMNLP profile must not leak AAAI format rules.
    assert "aaai2026.sty" not in p.text


def test_aaai_profile_resolves_to_real_prose() -> None:
    p = load_research_profile({"ARGUS_SKILL_RESEARCH_PROFILE": "aaai2026-tierharness"})
    assert p is not None
    assert p.name == "aaai2026-tierharness"
    assert "AAAI 2026 TierHarness project" in p.text
    # Carries the AAAI format addendum.
    assert "aaai2026.sty" in p.text
    assert "Never emit" in p.text
    assert "no mandatory Limitations" in p.text
    # Not the generic fallback.
    assert "No built-in profile text is available" not in p.text


def test_unknown_profile_falls_back() -> None:
    p = load_research_profile({"ARGUS_SKILL_RESEARCH_PROFILE": "neurips2027"})
    assert p is not None
    assert "No built-in profile text is available" in p.text


def test_no_profile_returns_none() -> None:
    assert load_research_profile({}) is None
