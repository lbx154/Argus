"""EMNLP and AAAI reviewer checklists are peers: each venue resolves its own
NATIVE checklist through the same path (no EMNLP-privileged branch, no
per-string patching, no cross-venue leakage)."""
from __future__ import annotations

import pytest

from argus_skill.skills.venue_profiles import (
    AAAI_PROFILE,
    EMNLP_PROFILE,
    FRONTIERS_SLEEP_PROFILE,
)
from argus_skill.verticals.research.stages import (
    REVIEWER_CHECKLISTS,
    REVIEWER_CHECKLISTS_BY_VENUE,
    completion_gate,
    reviewer_checklists_for,
)
from argus_skill.tools.stage_check import _reviewer_checklist_for


def test_both_venues_registered_as_peers():
    assert set(REVIEWER_CHECKLISTS_BY_VENUE) == {
        "EMNLP",
        "AAAI",
        "FRONTIERS_SLEEP",
    }
    # Back-compat alias defaults to EMNLP.
    assert REVIEWER_CHECKLISTS is REVIEWER_CHECKLISTS_BY_VENUE["EMNLP"]


def test_neutral_stages_shared_verbatim():
    e = reviewer_checklists_for(EMNLP_PROFILE)
    a = reviewer_checklists_for(AAAI_PROFILE)
    for stage in ("research", "plan", "benchmark", "analysis", "draft"):
        assert e[stage] is a[stage], f"{stage} should be shared venue-neutral"
    assert e["run"] == a["run"]


@pytest.mark.parametrize("stage", ["review", "submission"])
def test_format_stages_are_native_no_cross_leak(stage):
    e_skill, e_instr, _ = reviewer_checklists_for(EMNLP_PROFILE)[stage]
    a_skill, a_instr, _ = reviewer_checklists_for(AAAI_PROFILE)[stage]
    # The two venues' format-bearing stages are genuinely different.
    assert (a_skill, a_instr) != (e_skill, e_instr)
    # AAAI native NEVER leaks EMNLP/ACL tokens (skill filename or instructions).
    assert "EMNLP" not in a_instr and "EMNLP" not in a_skill
    assert "ACL" not in a_instr


def test_review_skill_is_native_per_venue():
    assert reviewer_checklists_for(EMNLP_PROFILE)["review"][0] == (
        "reviewer/emnlp-academic-language-review.md"
    )
    assert reviewer_checklists_for(AAAI_PROFILE)["review"][0] == (
        "reviewer/aaai-academic-language-review.md"
    )


def test_dispatch_goes_through_one_path_for_both():
    # _reviewer_checklist_for has no venue.key=="EMNLP" special case: both
    # venues resolve their own native tuple.
    for profile, expect in ((EMNLP_PROFILE, "EMNLP"), (AAAI_PROFILE, "AAAI")):
        _skill, instr, _files = _reviewer_checklist_for("submission", profile)
        assert f"actual {expect} reviewer" in instr


def test_unknown_venue_raises_not_silent_emnlp():
    with pytest.raises(KeyError):
        reviewer_checklists_for("NEURIPS")


def test_completion_gate_is_venue_neutral():
    assert completion_gate == "full_paper"


def test_frontiers_review_checklist_is_native_and_page_limit_free():
    skill, instructions, _files = reviewer_checklists_for(
        FRONTIERS_SLEEP_PROFILE
    )["review"]
    assert skill == "reviewer/academic-paper-peer-review-benchmark.md"
    assert "Frontiers in Sleep" in instructions
    assert "NO fixed page limit" in instructions
    assert "EMNLP" not in instructions
