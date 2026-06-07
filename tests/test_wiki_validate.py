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
        "data",
        "queries",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "data" / "schema.yaml").write_text("# schema\n", encoding="utf-8")
    (root / "query_pack.md").write_text("# pack\n", encoding="utf-8")
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


def test_validate_structure_flags_missing_schema(tmp_path: Path):
    root = tmp_path / ".autors" / "demo" / "wiki"
    (root / "sources" / "papers").mkdir(parents=True)
    (root / "pages" / "techniques").mkdir(parents=True)
    (root / "query_pack.md").write_text("# pack\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="schema.yaml"):
        validate_wiki(WikiStore(root))


def test_validate_structure_ok_for_initialized(tmp_path: Path):
    from argus_skill.wiki.bootstrap import init_wiki

    root = init_wiki("demo", base=tmp_path)
    validate_wiki(WikiStore(root))
