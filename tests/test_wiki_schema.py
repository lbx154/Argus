from __future__ import annotations

from datetime import date

import pytest

from argus_skill.wiki.schema import (
    PageCard,
    SourceNote,
    SourcePaper,
    SourceRun,
    parse_frontmatter,
    serialize_frontmatter,
)


def test_page_card_roundtrip():
    card = PageCard(
        id="tech-grpo-asym-clip-2026-06-04",
        type="technique",
        status="scratch",
        title="Asymmetric clipping in GRPO",
        tags=["grpo", "clipping"],
        sources=["papers/2406.12345.md"],
        related_runs=[],
        related_projects=[],
        confidence="low",
        revisit_after=date(2026, 9, 4),
        created_at=date(2026, 6, 4),
        last_reviewed_at=date(2026, 6, 4),
        reviewer_note="Worth watching.",
        body="Free-form prose.",
    )
    text = serialize_frontmatter(card)
    assert text.startswith("---\n")
    assert "type: technique" in text
    assert "status: scratch" in text
    assert text.endswith("Free-form prose.\n")

    parsed = parse_frontmatter(text, PageCard)
    assert parsed == card


def test_page_card_required_fields_enforced():
    with pytest.raises(ValueError, match="status"):
        PageCard(
            id="x",
            type="technique",
            status="bogus",  # invalid
            title="t",
            tags=[],
            sources=[],
            related_runs=[],
            related_projects=[],
            confidence="low",
            revisit_after=None,
            created_at=date(2026, 6, 4),
            last_reviewed_at=date(2026, 6, 4),
            reviewer_note="",
            body="",
        )


def test_source_paper_roundtrip():
    src = SourcePaper(
        id="papers/2406.12345",
        url="https://arxiv.org/abs/2406.12345",
        title="A paper",
        ingested_at=date(2026, 6, 4),
        ingested_by="paper-ingestion@mission-abc",
        checksum="sha256:abc",
        body="Verbatim abstract.",
    )
    text = serialize_frontmatter(src)
    assert "url: https://arxiv.org/abs/2406.12345" in text
    parsed = parse_frontmatter(text, SourcePaper)
    assert parsed == src


def test_source_run_with_metrics():
    run = SourceRun(
        id="runs/2026-06-04-abc",
        mission_id="abc",
        git_commit="d6f8520",
        project="bagel-rl",
        config_path="experiments/r1/cfg.yaml",
        dataset="vledit",
        metrics={"train_loss_final": 0.182, "eval_score": 0.41},
        artifacts={"curves": "experiments/r1/curves.png"},
        outcome="failure",
        failure_signature="nan-after-12k-grpo",
        suspected_cause="",
        next_action="",
        body="",
    )
    text = serialize_frontmatter(run)
    assert "failure_signature: nan-after-12k-grpo" in text
    parsed = parse_frontmatter(text, SourceRun)
    assert parsed == run


def test_source_note_roundtrip():
    note = SourceNote(
        id="notes/2026-06-06-stage-check-terminal",
        title="Stage check terminal blocker",
        mission_id="mission-abc",
        created_at=date(2026, 6, 6),
        tags=["stage-check", "operator-blocker"],
        body="Operational observation.",
    )
    text = serialize_frontmatter(note)
    assert "id: notes/2026-06-06-stage-check-terminal" in text
    parsed = parse_frontmatter(text, SourceNote)
    assert parsed == note
