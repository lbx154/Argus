from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.skills.stage_machine import format_stage_checklist
from argus_skill.skills.venue_profiles import (
    FRONTIERS_SLEEP_PROFILE,
    get_venue_profile,
    resolve_venue_profile,
)
from argus_skill.tools.stage_check import _reviewer_checklist_for
from argus_skill.verticals.research.academic_language_review import _review_prompt
from argus_skill.verticals.research.paper_layout_review import (
    _deterministic_assessment,
)


def _write_state(root: Path, venue: str) -> None:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "review", "target_venue": venue}),
        encoding="utf-8",
    )


def test_frontiers_tokens_resolve_to_canonical_profile() -> None:
    for token in (
        "FRONTIERS_SLEEP",
        "frontiers",
        "Frontiers in Sleep",
        "FRSLE",
    ):
        assert get_venue_profile(token) is FRONTIERS_SLEEP_PROFILE


def test_frontiers_profile_encodes_live_contract() -> None:
    p = FRONTIERS_SLEEP_PROFILE
    assert p.has_fixed_page_budget is False
    assert p.body_page_limit is None
    assert p.main_text_word_limit == 12_000
    assert p.documentclass == r"\documentclass[utf8]{FrontiersinHarvard}"
    assert p.bib_style == "Frontiers-Harvard"
    assert p.requires_single_spacing is True
    assert p.requires_line_numbers is True
    assert p.review_model == "single-anonymized"
    assert p.requires_real_author_metadata is True
    assert p.requires_ai_disclosure is True
    assert p.requires_figure_alt_text is True
    assert "no fixed page limit" in p.page_budget_line()


def test_unknown_nonempty_venue_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        get_venue_profile("Unknown Journal")
    _write_state(tmp_path, "Unknown Journal")
    with pytest.raises(KeyError):
        resolve_venue_profile(tmp_path)
    checklist = format_stage_checklist(
        "review", role="reviewer", project_root=tmp_path
    )
    assert "`venue.profile`" in checklist
    assert "Unknown Journal" in checklist
    assert "do not return `done`" in checklist
    assert "reads like a real EMNLP paper" not in checklist


def test_unknown_venue_skill_matcher_excludes_all_venue_skills(
    tmp_path: Path,
    caplog,
) -> None:
    from argus_skill.skills.venue_profiles import venue_excluded_skill_files

    _write_state(tmp_path, "Unknown Journal")
    excluded = venue_excluded_skill_files(tmp_path)
    assert "emnlp-paper-drafting.md" in excluded
    assert "aaai-paper-drafting.md" in excluded
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_missing_venue_excludes_venue_skills_without_error(
    tmp_path: Path,
    caplog,
    monkeypatch,
) -> None:
    from argus_skill.skills.venue_profiles import venue_excluded_skill_files

    monkeypatch.delenv("ARGUS_SKILL_VENUE", raising=False)
    excluded = venue_excluded_skill_files(tmp_path)

    assert "emnlp-paper-drafting.md" in excluded
    assert "aaai-paper-drafting.md" in excluded
    assert not any(record.levelname == "ERROR" for record in caplog.records)


def test_frontiers_project_uses_native_reviewer_rules(tmp_path: Path) -> None:
    _write_state(tmp_path, "Frontiers in Sleep")
    profile = resolve_venue_profile(tmp_path)
    assert profile is FRONTIERS_SLEEP_PROFILE
    skill, instructions, _files = _reviewer_checklist_for("review", profile)
    assert skill == "reviewer/academic-paper-peer-review-benchmark.md"
    assert "Frontiers in Sleep" in instructions
    assert "NO fixed page limit" in instructions
    assert "single spacing" in instructions
    assert "AI disclosure" in instructions
    assert "alt text" in instructions
    assert "EMNLP" not in instructions

    submission = format_stage_checklist(
        "submission", role="reviewer", project_root=tmp_path
    )
    assert "real author names" in submission
    assert "no real author names" not in submission
    assert "anonymous Frontiers" not in submission

    draft = format_stage_checklist(
        "draft", role="reviewer", project_root=tmp_path
    )
    review = format_stage_checklist(
        "review", role="reviewer", project_root=tmp_path
    )
    assert "Hypothesis and Theory sections" in draft
    assert "cross-benchmark results table" not in review
    assert "evaluated model/backend" not in review
    assert "prior-theory paragraph" in review


def test_frontiers_academic_prompt_has_word_not_page_limit() -> None:
    prompt = _review_prompt(
        source_text_by_path={"paper/main.tex": "x"},
        deterministic={"k": 1},
        threshold=4.0,
        venue=FRONTIERS_SLEEP_PROFILE,
    )
    assert "Frontiers in Sleep Hypothesis and Theory article" in prompt
    assert "no fixed page limit" in prompt
    assert "12,000 words" in prompt
    assert "EMNLP long paper" not in prompt
    assert "eight-page body budget" not in prompt
    assert "benchmark families" in prompt
    assert "Do not require agent controllers" in prompt


def test_frontiers_layout_extracts_real_top_level_pages() -> None:
    layout = "\f".join(
        [
            "ABSTRACT\nConclusion: bounded implication.",
            "body",
            "body",
            "body",
            "body",
            "body",
            "body",
            "223  10 CONCLUSION",
            "9. REFERENCES\nSmith (2024)",
            "more references",
        ]
    )
    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text=layout,
        threshold=3.5,
        venue=FRONTIERS_SLEEP_PROFILE,
    )
    page_flow = result["page_flow_contract"]
    assert page_flow["conclusion_page"] == 8
    assert page_flow["references_page"] == 9
    codes = {issue["code"] for issue in result["issues"]}
    assert "rendered_main_body_underfilled" not in codes
    assert "conclusion_after_page_8" not in codes
    assert "references_before_full_body" not in codes


def test_nested_heading_is_not_top_level_conclusion() -> None:
    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="3.2 Conclusion\nsubsection prose\fREFERENCES",
        threshold=3.5,
        venue=FRONTIERS_SLEEP_PROFILE,
    )
    assert result["page_flow_contract"]["conclusion_page"] is None


def test_frontiers_does_not_require_references_on_separate_page() -> None:
    result = _deterministic_assessment(
        tex_text="",
        log_text="",
        layout_text="CONCLUSION\nbounded close\nREFERENCES\nSmith (2024)",
        threshold=3.5,
        venue=FRONTIERS_SLEEP_PROFILE,
    )
    codes = {issue["code"] for issue in result["issues"]}
    assert result["page_flow_contract"]["conclusion_page"] == 1
    assert result["page_flow_contract"]["references_page"] == 1
    assert "references_share_body_page" not in codes
