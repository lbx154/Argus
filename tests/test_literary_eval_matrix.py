"""Loop 12: the capability matrix is honest and checkable — final acceptance.

Asserts every A/B capability points at a test file that actually exists, that no
live-model / gap concept is smuggled into tier A (the anti-fake-green invariant),
that the gaps are documented, and that all five verticals + the shared layer are
covered. The full regression (run separately) proves the referenced tests pass.
"""
from __future__ import annotations

from pathlib import Path

from argus_skill.literary.eval_matrix import (
    CAPABILITIES,
    TIERS,
    Capability,
    by_tier,
    gaps,
    render_matrix,
)
from argus_skill.literary.eval_matrix import _LIVE_ONLY_TERMS

_REPO = Path(__file__).resolve().parents[1]


def test_every_capability_has_a_valid_tier():
    for c in CAPABILITIES:
        assert c.tier in TIERS, f"{c.name}: bad tier {c.tier!r}"


def test_A_and_B_evidence_files_exist():
    for c in CAPABILITIES:
        if c.tier in ("A", "B"):
            p = _REPO / c.evidence
            assert p.is_file(), f"{c.tier} capability {c.name!r} evidence missing: {c.evidence}"


def test_no_live_or_gap_concept_is_claimed_as_tier_A():
    # the anti-fake-green invariant: aesthetics / viewpoint / literariness are
    # never dressed up as a machine-decidable (A) capability.
    a_names = " ".join(c.name.lower() for c in by_tier()["A"])
    for term in _LIVE_ONLY_TERMS:
        assert term not in a_names, f"live/gap term {term!r} appears in a tier-A row"


def test_viewpoint_drift_is_a_documented_gap_not_A():
    vp = [c for c in CAPABILITIES if "viewpoint" in c.name.lower()]
    assert vp, "viewpoint/tense drift should be listed"
    assert all(c.tier == "GAP" for c in vp)


def test_gaps_are_present_and_documented():
    g = gaps()
    assert g, "the matrix must document at least one honest capability gap"
    assert all(c.evidence.strip() for c in g)


def test_all_verticals_and_shared_are_covered():
    domains = " ".join(c.domain for c in CAPABILITIES)
    for v in ("fiction_writing", "classical_poetry", "modern_poetry", "prose",
              "literary_editor", "shared"):
        assert v in domains, f"{v} not covered by the matrix"


def test_each_tier_is_non_empty():
    grouped = by_tier()
    for t in TIERS:
        assert grouped[t], f"tier {t} is empty"


def test_render_matrix_is_nonempty():
    out = render_matrix()
    assert "Capability Matrix" in out
    assert "deterministic" in out and "not implemented" in out
