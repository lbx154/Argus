from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.stage_machine import (
    format_full_pipeline_checklist,
    format_stage_checklist,
)


def _project(tmp_path: Path, venue: str | None) -> Path:
    (tmp_path / ".argus").mkdir(parents=True, exist_ok=True)
    payload = {
        "vertical": "research",
        "current_stage": "paper",
        "selected_idea": None,
        "current_verdict": "in_progress",
        "next_action": "write",
    }
    if venue is not None:
        payload["target_venue"] = venue
    (tmp_path / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return tmp_path


def test_missing_venue_does_not_require_a_profile_file(tmp_path: Path) -> None:
    paper = format_stage_checklist(
        "paper",
        role="reviewer",
        project_root=_project(tmp_path, None),
    )
    assert "paper.work_products" in paper
    assert "selected venue's current official rules" in paper
    assert "VENUE_PROFILE" not in paper
    assert "`venue.profile`" not in paper


def test_explicit_venue_uses_the_same_single_handoff_contract(
    tmp_path: Path,
) -> None:
    paper = format_stage_checklist(
        "paper",
        role="reviewer",
        project_root=_project(tmp_path, "AAAI"),
    )
    assert "paper.handoff" in paper
    assert "HANDOFF.md" in paper
    assert "VENUE_PROFILE" not in paper


def test_unknown_venue_does_not_block_review_with_profile_artifacts(
    tmp_path: Path,
) -> None:
    review = format_stage_checklist(
        "review",
        role="reviewer",
        project_root=_project(tmp_path, "Undecided pending contribution strength"),
    )
    assert "review.terminal" in review
    assert "paper/REVIEW.md" in review
    assert "`venue.profile`" not in review


def test_full_pipeline_has_only_four_research_stages(tmp_path: Path) -> None:
    checklist = format_full_pipeline_checklist(
        role="reviewer",
        project_root=_project(tmp_path, "EMNLP"),
    )
    for stage in ("idea", "experiment", "paper", "review"):
        assert f"### {stage}" in checklist
    assert "### build" not in checklist
    assert "### submission" not in checklist
    assert "VENUE_PROFILE" not in checklist
