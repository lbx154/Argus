"""Venue-awareness of the framework floor checklists.

The floor is authored EMNLP-first and project checklist edits cannot relax it, so the
AAAI venue switch must happen in the floor itself. EMNLP must render the floor
byte-identically; AAAI must rewrite the page budget, end-matter order, and the
anonymity block.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.stage_machine import (
    format_full_pipeline_checklist,
    format_stage_checklist,
)


def _project(tmp_path: Path, venue: str | None) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    payload = {"current_stage": "draft"}
    if venue is not None:
        payload["target_venue"] = venue
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return tmp_path


def test_missing_venue_blocks_instead_of_defaulting_to_emnlp(
    tmp_path: Path,
) -> None:
    unresolved = format_stage_checklist(
        "draft", role="reviewer", project_root=_project(tmp_path / "a", None)
    )
    assert "`venue.profile`" in unresolved
    assert "CCF-A" in unresolved
    assert "Anonymous EMNLP Submission" not in unresolved


def test_explicit_emnlp_floor_remains_available(tmp_path: Path) -> None:
    emnlp = format_stage_checklist(
        "draft", role="reviewer", project_root=_project(tmp_path, "EMNLP")
    )
    assert "EMNLP 2026 two-column paper sections" in emnlp
    assert "Conclusion, Limitations, Ethical Considerations" in emnlp
    assert "up to 8 pages" in emnlp
    assert "References starts on page 9 or later" in emnlp


def test_aaai_floor_rewrites_page_budget_and_sections(tmp_path: Path) -> None:
    root = _project(tmp_path, "AAAI")
    draft = format_stage_checklist("draft", role="reviewer", project_root=root)
    assert "AAAI 2026 two-column paper sections" in draft
    assert "up to 7 pages, References starts on page 8 or later" in draft
    # AAAI does not mandate Limitations/Ethics as a body end section.
    assert "Limitations, Ethics, Reproducibility appendix" not in draft
    assert "Reproducibility Checklist" in draft
    # No leftover EMNLP page-9 floor.
    assert "References starts on page 9 or later" not in draft


def test_aaai_submission_anonymity_block(tmp_path: Path) -> None:
    root = _project(tmp_path, "AAAI")
    sub = format_stage_checklist("submission", role="reviewer", project_root=root)
    assert "Anonymous submission" in sub
    assert "aaai2026 submission mode" in sub
    assert "Anonymous EMNLP Submission" not in sub


def test_full_pipeline_checklist_is_venue_aware(tmp_path: Path) -> None:
    aaai = format_full_pipeline_checklist(role="reviewer", project_root=_project(tmp_path, "AAAI"))
    assert "Anonymous submission" in aaai
    assert "up to 7 pages" in aaai
    assert "Anonymous EMNLP Submission" not in aaai


def test_unknown_venue_does_not_break_venue_neutral_plan(tmp_path: Path) -> None:
    root = _project(tmp_path, "Undecided pending contribution strength")
    plan = format_stage_checklist("plan", role="reviewer", project_root=root)
    assert "Experiment plan states the hypothesis" in plan
    assert "`venue.profile`" not in plan


def test_unknown_venue_blocks_full_pipeline_without_emnlp_fallback(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "Undecided pending contribution strength")
    checklist = format_full_pipeline_checklist(
        role="reviewer", project_root=root
    )
    assert "`venue.profile`" in checklist
    assert "do not return `done`" in checklist
    assert "Anonymous EMNLP Submission" not in checklist
