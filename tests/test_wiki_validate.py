from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from argus_skill.wiki.schema import PageCard, SourcePaper
from argus_skill.wiki.store import WikiStore
from argus_skill.wiki.validate import ValidationError, validate_wiki


@pytest.fixture
def wiki(tmp_path: Path) -> WikiStore:
    root = tmp_path / ".autors" / "demo" / "wiki"
    for sub in (
        "sources/papers",
        "sources/repos",
        "sources/runs",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return WikiStore(root)


def test_clean_wiki_validates(wiki: WikiStore):
    src = SourcePaper(
        id="papers/2406.12345",
        url="https://arxiv.org/abs/2406.12345",
        title="t",
        ingested_at=date(2026, 6, 4),
        ingested_by="x",
        checksum="sha256:abc",
        body="",
    )
    wiki.write_source(src)
    card = PageCard(
        id="tech-x",
        type="technique",
        status="scratch",
        title="x",
        tags=["grpo"],
        sources=["papers/2406.12345.md"],
        related_runs=[],
        related_projects=[],
        confidence="low",
        revisit_after=None,
        created_at=date(2026, 6, 4),
        last_reviewed_at=date(2026, 6, 4),
        reviewer_note="",
        body="",
    )
    wiki.write_page(card)
    validate_wiki(wiki)  # should not raise


def test_dangling_source_ref_fails(wiki: WikiStore):
    card = PageCard(
        id="tech-x",
        type="technique",
        status="scratch",
        title="x",
        tags=[],
        sources=["papers/9999.99999.md"],  # does not exist
        related_runs=[],
        related_projects=[],
        confidence="low",
        revisit_after=None,
        created_at=date(2026, 6, 4),
        last_reviewed_at=date(2026, 6, 4),
        reviewer_note="",
        body="",
    )
    wiki.write_page(card)
    with pytest.raises(ValidationError, match="dangling source"):
        validate_wiki(wiki)
