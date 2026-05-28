"""Tests for the stage-aware reviewer checklist module."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.skills.stage_checklists import (
    CANONICAL_STAGE_ORDER,
    STAGE_CHECKLISTS,
    current_stage,
    format_full_pipeline_checklist,
    format_stage_checklist,
    get_stage_checklist,
    list_stages,
)


def test_canonical_stage_order_covers_eight_stages() -> None:
    assert CANONICAL_STAGE_ORDER == (
        "research",
        "plan",
        "benchmark",
        "run",
        "analysis",
        "draft",
        "review",
        "submission",
    )
    assert list_stages() == CANONICAL_STAGE_ORDER


def test_every_canonical_stage_has_a_checklist() -> None:
    """No stage may ship without at least one checklist item; otherwise the
    reviewer prompt collapses to an empty body for that stage.
    """

    for stage in CANONICAL_STAGE_ORDER:
        items = get_stage_checklist(stage)
        assert items, f"stage {stage!r} has no checklist items"
        for item in items:
            assert item.id.startswith(f"{stage}.")
            assert item.statement
            assert item.evidence_hint


def test_format_stage_checklist_engineer_framing() -> None:
    text = format_stage_checklist("research", role="engineer")
    assert "## Stage checklist (research)" in text
    assert "L2 reviewer will tick these items" in text
    assert "research.literature" in text
    # No retired CLI command leaks into the prompt.
    assert "validate-full-emnlp" not in text
    assert "validate-grounding" not in text


def test_format_stage_checklist_reviewer_framing() -> None:
    text = format_stage_checklist("draft", role="reviewer")
    assert "## Stage checklist (draft)" in text
    assert "You are the L2 reviewer" in text
    assert "Do not run any `validate-*` shell command" in text


def test_format_stage_checklist_unknown_stage_returns_safe_block() -> None:
    text = format_stage_checklist("nonexistent_stage", role="engineer")
    # Should not crash and should communicate that there's no checklist.
    assert "Stage checklist (nonexistent_stage)" in text
    assert "No checklist is defined" in text


def test_format_full_pipeline_checklist_concatenates_every_stage() -> None:
    text = format_full_pipeline_checklist(role="reviewer")
    assert "Full pipeline checklist (final submission gate)" in text
    for stage in CANONICAL_STAGE_ORDER:
        # Section header should appear for every stage.
        assert f"### {stage}" in text, f"final-gate prompt missing stage section {stage!r}"
    # No retired CLI command leaks anywhere in the final-gate block.
    for retired in (
        "validate-full-emnlp",
        "validate-grounding",
        "validate-paper-contract",
        "refresh-manifest",
    ):
        assert retired not in text, f"final-gate prompt still mentions retired tool {retired!r}"


def test_current_stage_defaults_to_research_when_state_missing(tmp_path: Path) -> None:
    assert current_stage(tmp_path) == "research"


def test_current_stage_reads_pipeline_state(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "benchmark"}),
        encoding="utf-8",
    )
    assert current_stage(tmp_path) == "benchmark"


def test_current_stage_clamps_unknown_stage_to_research(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": "made_up_stage"}),
        encoding="utf-8",
    )
    assert current_stage(tmp_path) == "research"


def test_current_stage_tolerates_malformed_json(tmp_path: Path) -> None:
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "PIPELINE_STATE.json").write_text("{not json", encoding="utf-8")
    assert current_stage(tmp_path) == "research"


def test_stage_checklist_completeness() -> None:
    """Each stage's checklist should cover the irreducible quality bars
    that historically were enforced by Python validators. This test acts
    as a regression guard: if someone deletes an item the reviewer would
    no longer notice the missing artifact.
    """

    research_ids = {item.id for item in STAGE_CHECKLISTS["research"]}
    assert "research.literature" in research_ids
    assert "research.go_no_go" in research_ids

    run_ids = {item.id for item in STAGE_CHECKLISTS["run"]}
    assert "run.matrix" in run_ids
    assert "run.scale" in run_ids

    analysis_ids = {item.id for item in STAGE_CHECKLISTS["analysis"]}
    assert "analysis.claims" in analysis_ids

    draft_ids = {item.id for item in STAGE_CHECKLISTS["draft"]}
    assert "draft.pdf" in draft_ids
    assert "draft.bibliography" in draft_ids

    review_ids = {item.id for item in STAGE_CHECKLISTS["review"]}
    assert "review.infrastructure" in review_ids
    assert "review.placeholders" in review_ids

    submission_ids = {item.id for item in STAGE_CHECKLISTS["submission"]}
    assert "submission.upstream" in submission_ids
    assert "submission.anonymous" in submission_ids
