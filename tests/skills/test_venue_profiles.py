from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.venue_profiles import (
    AAAI_PROFILE,
    DEFAULT_VENUE_KEY,
    EMNLP_PROFILE,
    FRONTIERS_SLEEP_PROFILE,
    cross_venue_excluded_skill_files,
    get_venue_profile,
    resolve_venue_profile,
    venue_excluded_skill_files,
)


def test_emnlp_profile_reproduces_current_constants() -> None:
    p = EMNLP_PROFILE
    assert p.key == "EMNLP"
    assert (p.conclusion_underfill_page, p.conclusion_max_page, p.references_min_page) == (7, 8, 9)
    assert p.anon_author_string == "Anonymous EMNLP Submission"
    assert p.academic_language_rubric_id == "emnlp-academic-language-v2"
    assert p.emit_bibliographystyle is True
    assert p.mandatory_end_sections == ("Limitations", "Ethical Considerations")
    assert p.requires_pdfinfo is False
    assert p.forbidden_packages == ()


def test_aaai_variant_tokens_resolve_to_aaai() -> None:
    # R5-2: a planner naturally writes "aaai2026" / "AAAI 2026" / "AAAI-26" -- all
    # must resolve to the AAAI profile, not silently fall back to EMNLP (which would
    # grade an AAAI paper by EMNLP rules -- the exact failure this seam exists to stop).
    for token in ("AAAI", "aaai", "aaai2026", "AAAI 2026", "AAAI-26", "AAAI2026", "aaai-2026"):
        assert get_venue_profile(token).key == AAAI_PROFILE.key, token
    # EMNLP variants still resolve to EMNLP; empty preserves the historical
    # default while unknown non-empty values fail closed.
    assert get_venue_profile("EMNLP 2026").key == EMNLP_PROFILE.key
    with pytest.raises(KeyError):
        get_venue_profile("NeurIPS")
    assert get_venue_profile("").key == DEFAULT_VENUE_KEY


def test_aaai_profile_matches_verified_facts() -> None:
    p = AAAI_PROFILE
    assert p.key == "AAAI"
    assert (p.conclusion_underfill_page, p.conclusion_max_page, p.references_min_page) == (6, 7, 8)
    assert p.body_page_limit == 7
    assert p.anon_author_string == "Anonymous submission"
    assert p.style_package == "aaai2026"
    assert p.review_mode_macro == r"\usepackage[submission]{aaai2026}"
    # AAAI: the class sets the bibstyle; emitting one is an error.
    assert p.emit_bibliographystyle is False
    assert p.requires_pdfinfo is True
    assert p.requires_style_package is True
    assert p.forbids_nocopyright is True
    assert p.requires_reproducibility_checklist is True
    assert "hyperref" in p.forbidden_packages and "navigator" in p.forbidden_packages
    # AAAI does not mandate Limitations/Ethics.
    assert p.mandatory_end_sections == ()
    assert p.abstract_word_floor_is_hard is False


def test_get_venue_profile_is_case_and_alias_insensitive() -> None:
    assert get_venue_profile("aaai") is AAAI_PROFILE
    assert get_venue_profile("AAAI") is AAAI_PROFILE
    assert get_venue_profile("emnlp") is EMNLP_PROFILE
    assert get_venue_profile("acl") is EMNLP_PROFILE  # alias
    assert get_venue_profile("ARR") is EMNLP_PROFILE  # alias
    # Empty -> default; unknown non-empty -> fail closed.
    assert get_venue_profile(None).key == DEFAULT_VENUE_KEY
    with pytest.raises(KeyError):
        get_venue_profile("nope")


def _write_state(root: Path, payload: dict) -> None:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_resolve_defaults_to_emnlp_when_field_absent(tmp_path: Path) -> None:
    _write_state(tmp_path, {"current_stage": "plan"})
    assert resolve_venue_profile(tmp_path) is EMNLP_PROFILE
    # no PIPELINE_STATE at all -> still EMNLP
    assert resolve_venue_profile(tmp_path / "nonexistent") is EMNLP_PROFILE


def test_resolve_reads_target_venue(tmp_path: Path) -> None:
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "AAAI"})
    assert resolve_venue_profile(tmp_path) is AAAI_PROFILE


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "EMNLP"})
    monkeypatch.setenv("ARGUS_SKILL_VENUE", "aaai")
    assert resolve_venue_profile(tmp_path) is AAAI_PROFILE


def test_cross_venue_exclusion_hides_other_venue_skills() -> None:
    # An EMNLP project hides the AAAI siblings; an AAAI project hides the EMNLP ones.
    emnlp_excl = cross_venue_excluded_skill_files(EMNLP_PROFILE)
    aaai_excl = cross_venue_excluded_skill_files(AAAI_PROFILE)
    assert emnlp_excl == {
        "aaai-paper-drafting.md",
        "aaai-format-preflight.md",
        "aaai-paper-skill-router.md",
        "aaai-academic-language-review.md",
    }
    assert aaai_excl == {
        "emnlp-paper-drafting.md",
        "emnlp-format-preflight.md",
        "emnlp-paper-skill-router.md",
        "emnlp-academic-language-review.md",
    }
    # Venue-neutral skills (e.g. infrastructure review) are never excluded.
    assert not any("infrastructure" in f for f in emnlp_excl | aaai_excl)


def test_venue_excluded_skill_files_resolves_from_project(tmp_path: Path) -> None:
    _write_state(tmp_path, {"target_venue": "AAAI"})
    excl = venue_excluded_skill_files(tmp_path)
    assert "emnlp-paper-drafting.md" in excl
    assert "aaai-paper-drafting.md" not in excl
    # Default (no target_venue) is EMNLP -> excludes the AAAI siblings.
    _write_state(tmp_path / "e", {})
    assert "aaai-paper-drafting.md" in venue_excluded_skill_files(tmp_path / "e")


def test_venue_skill_files_resolve_to_real_builtin_skills() -> None:
    # Guards against rename drift: if a venue skill .md is renamed without
    # updating venue_skill_files, the cross-venue exclusion silently becomes a
    # no-op. Assert every listed basename exists as a real built-in skill file.
    from argus_skill.skills.builtins import iter_builtin_skill_texts

    on_disk = {Path(p).name for p, _ in iter_builtin_skill_texts()}
    for profile in (EMNLP_PROFILE, AAAI_PROFILE, FRONTIERS_SLEEP_PROFILE):
        for fname in profile.venue_skill_files:
            assert fname in on_disk, (
                f"{profile.key} venue_skill_files lists {fname!r} but no such "
                "builtin skill exists — the matcher exclusion would be a no-op"
            )
