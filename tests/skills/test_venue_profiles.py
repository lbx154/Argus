from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.verticals.research.venue_profiles import (
    AAAI_PROFILE,
    EMNLP_PROFILE,
    get_venue_profile,
    resolve_venue_profile,
)


def test_emnlp_profile_reproduces_current_constants() -> None:
    p = EMNLP_PROFILE
    assert p.key == "EMNLP"
    assert (p.conclusion_underfill_page, p.conclusion_max_page, p.references_min_page) == (7, 8, 9)
    assert p.anon_author_string == "Anonymous EMNLP Submission"
    assert p.academic_language_rubric_id == "venue-academic-language-v1"
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
    # EMNLP variants still resolve to EMNLP; empty and unknown values fail closed.
    assert get_venue_profile("EMNLP 2026").key == EMNLP_PROFILE.key
    with pytest.raises(KeyError):
        get_venue_profile("NeurIPS")
    with pytest.raises(KeyError):
        get_venue_profile("")


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
    # Empty and unknown values fail closed.
    with pytest.raises(KeyError):
        get_venue_profile(None)
    with pytest.raises(KeyError):
        get_venue_profile("nope")


def _write_state(root: Path, payload: dict) -> None:
    (root / ".argus").mkdir(parents=True, exist_ok=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_resolve_requires_explicit_or_local_profile_when_field_absent(
    tmp_path: Path,
) -> None:
    _write_state(tmp_path, {"current_stage": "plan"})
    with pytest.raises(KeyError):
        resolve_venue_profile(tmp_path)
    with pytest.raises(KeyError):
        resolve_venue_profile(tmp_path / "nonexistent")


def test_resolve_reads_target_venue(tmp_path: Path) -> None:
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "AAAI"})
    assert resolve_venue_profile(tmp_path) is AAAI_PROFILE


def test_documented_validation_command_accepts_string_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_state(tmp_path, {"current_stage": "research", "target_venue": "AAAI"})
    monkeypatch.chdir(tmp_path)

    assert resolve_venue_profile(".") is AAAI_PROFILE


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_state(tmp_path, {"current_stage": "plan", "target_venue": "EMNLP"})
    monkeypatch.setenv("ARGUS_SKILL_VENUE", "aaai")
    assert resolve_venue_profile(tmp_path) is AAAI_PROFILE
