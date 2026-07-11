"""End-to-end: bootstrap -> ingest -> curate -> index -> validate."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.index import rebuild_indexes
from argus_skill.wiki.schema import PageCard, SourcePaper, SourceRun
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.validate import validate_wiki

pytestmark = pytest.mark.e2e


def test_full_loop(tmp_path: Path):
    # 1. operator: argus-skill wiki init demo
    root = init_wiki("demo", base=tmp_path)
    store = WikiStore(root)

    # 2. engineer (mission A): ingests a paper
    paper = SourcePaper(
        id="papers/2406.12345",
        url="https://arxiv.org/abs/2406.12345",
        title="Asymmetric clipping",
        ingested_at=date(2026, 6, 4),
        ingested_by="paper-ingestion@mission-A",
        checksum="sha256:abc",
        body="abstract text",
    )
    store.write_source(paper)

    # 3. engineer (mission A): writes RunCard
    run = SourceRun(
        id="runs/2026-06-04-A",
        mission_id="A",
        git_commit="d6f8520",
        project="demo",
        config_path="cfg.yaml",
        dataset="vledit",
        metrics={"train_loss_final": 0.18},
        artifacts={"curves": "curves.png"},
        outcome="failure",
        failure_signature="nan-12k-grpo",
        suspected_cause="",
        next_action="",
        body="",
    )
    store.write_source(run)

    # 4. reviewer (wiki-curator): synthesizes a technique card from the paper
    card = PageCard(
        id="tech-asym-clip",
        type="technique",
        status="scratch",
        title="Asymmetric clipping",
        tags=["grpo", "clipping"],
        sources=["papers/2406.12345.md"],
        related_runs=[],
        related_projects=[],
        revisit_after=date(2026, 3, 1),  # already overdue -> must surface as stale
        created_at=date(2026, 6, 4),
        last_reviewed_at=date(2026, 6, 4),
        reviewer_note="worth testing",
        body="",
    )
    store.write_page(card)

    # 5. reviewer promotes to candidate after second look
    card.status = "candidate"
    store.write_page(card)

    # 6. reviewer (wiki-curator): regenerate indexes + validate
    rebuild_indexes(store, today=date(2026, 6, 4))
    validate_wiki(store)

    # 7. assertions a planner consuming the wiki would care about
    by_status = (root / "queries" / "by-status.md").read_text()
    assert "candidate" in by_status and "tech-asym-clip" in by_status

    stale = (root / "queries" / "stale-watchlist.md").read_text()
    assert "tech-asym-clip" in stale  # overdue, status=candidate

    open_c = (root / "queries" / "open-contradictions.md").read_text()
    assert "None" in open_c  # no conflict cards yet
