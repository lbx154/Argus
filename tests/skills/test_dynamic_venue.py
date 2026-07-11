"""Dynamic (researched) venue support: a non-built-in target venue gets a
project-local VENUE_PROFILE.json, loaded by resolve_venue_profile and graded by
build_reviewer_checklists — without EMNLP/ACL leakage, and without disturbing the
built-in EMNLP/AAAI venues."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from argus_skill.skills.venue_profiles import (
    AAAI_PROFILE,
    EMNLP_PROFILE,
    VenueProfile,
    is_builtin_venue,
    load_local_venue_profile,
    resolve_venue_profile,
    venue_profile_path,
    write_venue_profile,
)
from argus_skill.verticals.research.stages import (
    REVIEWER_CHECKLISTS_EMNLP,
    build_reviewer_checklists,
    reviewer_checklists_for,
)
from argus_skill.skills.venue_research import (
    needs_venue_research,
    research_venue_profile,
)


def _neurips() -> VenueProfile:
    return VenueProfile(
        key="NEURIPS",
        display_name="NeurIPS 2026",
        body_page_limit=9,
        conclusion_underfill_page=8,
        conclusion_max_page=9,
        references_min_page=10,
        style_package="neurips_2026",
        style_files=("neurips_2026.sty",),
        reviewer_persona="NeurIPS",
        figure_style_persona="NeurIPS",
        mandatory_end_sections=(),
    )


def _project(tmp_path: Path, target_venue: str) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"vertical": "research", "target_venue": target_venue})
    )
    return tmp_path


# ---- (de)serialization ----------------------------------------------------

@pytest.mark.parametrize("profile", [EMNLP_PROFILE, AAAI_PROFILE])
def test_venue_profile_json_round_trip(profile):
    rt = VenueProfile.from_dict(json.loads(json.dumps(profile.to_dict())))
    assert rt == profile


def test_from_dict_requires_key_and_pages():
    with pytest.raises(ValueError):
        VenueProfile.from_dict({"display_name": "X 2026"})  # missing key
    with pytest.raises(ValueError):
        VenueProfile.from_dict("not a dict")


def test_write_and_load_local_profile(tmp_path):
    p = write_venue_profile(tmp_path, _neurips())
    assert p == venue_profile_path(tmp_path) and p.is_file()
    assert load_local_venue_profile(tmp_path) == _neurips()


def test_corrupt_local_profile_is_ignored(tmp_path):
    venue_profile_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    venue_profile_path(tmp_path).write_text("{ not json")
    assert load_local_venue_profile(tmp_path) is None  # fail-soft


# ---- resolution precedence ------------------------------------------------

def test_is_builtin_venue():
    assert is_builtin_venue("EMNLP") and is_builtin_venue("ACL")  # alias
    assert is_builtin_venue("aaai2026")  # variant
    assert not is_builtin_venue("NeurIPS") and not is_builtin_venue("")


def test_local_profile_beats_target_venue(tmp_path):
    root = _project(tmp_path, "NeurIPS")
    write_venue_profile(root, _neurips())
    resolved = resolve_venue_profile(root)
    assert resolved.key == "NEURIPS" and resolved.body_page_limit == 9


def test_unknown_venue_without_profile_fails_closed(tmp_path):
    root = _project(tmp_path, "NeurIPS")  # no VENUE_PROFILE.json
    with pytest.raises(KeyError, match="matched no known profile"):
        resolve_venue_profile(root)


# ---- dynamic reviewer checklists ------------------------------------------

def test_build_reviewer_checklists_is_venue_native_no_leak():
    cl = build_reviewer_checklists(_neurips())
    sub = cl["submission"][1]
    rev = cl["review"][1]
    assert "NeurIPS reviewer" in sub and "NeurIPS format" in sub
    assert "≤9 pages" in sub and "≤9 pages" in rev
    assert "EMNLP" not in sub and "ACL" not in sub
    # neutral stages are shared verbatim with the built-in dict
    assert cl["research"] is REVIEWER_CHECKLISTS_EMNLP["research"]


def test_reviewer_checklists_for_dispatch():
    # built-in -> native (unchanged)
    assert reviewer_checklists_for(EMNLP_PROFILE)["submission"] == (
        REVIEWER_CHECKLISTS_EMNLP["submission"]
    )
    # dynamic VenueProfile -> built from profile
    assert "NeurIPS reviewer" in reviewer_checklists_for(_neurips())["submission"][1]
    # bare unknown string (no profile) -> raises (no silent EMNLP fallback)
    with pytest.raises(KeyError):
        reviewer_checklists_for("NEURIPS")


# ---- research hook (mock runner, no network) ------------------------------

def test_needs_venue_research(tmp_path):
    assert needs_venue_research(_project(tmp_path / "a", "NeurIPS")) is True
    assert needs_venue_research(_project(tmp_path / "b", "AAAI")) is False
    cached = _project(tmp_path / "c", "NeurIPS")
    write_venue_profile(cached, _neurips())
    assert needs_venue_research(cached) is False


def test_research_venue_profile_writes_and_resolves(tmp_path):
    root = _project(tmp_path, "ICML")

    class MockRunner:
        def run_exec(self, *, prompt, options, run_label):
            assert options.live_search is True and run_label == "venue-research"
            write_venue_profile(
                root,
                VenueProfile(
                    key="ICML", display_name="ICML 2026", body_page_limit=8,
                    conclusion_underfill_page=7, conclusion_max_page=8,
                    references_min_page=9, reviewer_persona="ICML",
                ),
            )
            return type("R", (), {"exit_code": 0, "agent_messages": ["done"]})()

    assert research_venue_profile(MockRunner(), root) is True
    assert resolve_venue_profile(root).key == "ICML"


def test_research_venue_profile_fail_open(tmp_path):
    root = _project(tmp_path, "ICML")

    class BadRunner:
        def run_exec(self, **_):
            raise RuntimeError("boom")

    # never raises; no profile produced -> False
    assert research_venue_profile(BadRunner(), root) is False
    assert load_local_venue_profile(root) is None
