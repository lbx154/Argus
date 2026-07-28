from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from argus_skill.wiki.schema import PageCard, SourceNote, SourcePaper
from argus_skill.wiki.store import (
    WikiStore,
    wiki_root_for_project,
)


@pytest.fixture
def tmp_wiki(tmp_path: Path) -> Path:
    root = tmp_path / ".autors" / "demo" / "wiki"
    for sub in (
        "sources/papers",
        "sources/repos",
        "sources/runs",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
        "queries",
        "data",
        "scripts",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def test_wiki_root_for_project_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autors" / "myproj" / "wiki").mkdir(parents=True)
    assert wiki_root_for_project("myproj", base=tmp_path) == (
        tmp_path / ".autors" / "myproj" / "wiki"
    )


def test_write_and_read_source_paper(tmp_wiki: Path):
    store = WikiStore(tmp_wiki)
    src = SourcePaper(
        id="papers/2406.12345",
        url="https://arxiv.org/abs/2406.12345",
        title="A paper",
        ingested_at=date(2026, 6, 4),
        ingested_by="paper-ingestion@mission-abc",
        checksum="sha256:abc",
        body="Verbatim abstract.",
    )
    path = store.write_source(src)
    assert path == tmp_wiki / "sources" / "papers" / "2406.12345.md"
    assert path.exists()
    loaded = store.read_source(SourcePaper, "papers/2406.12345")
    assert loaded == src


def test_write_source_is_immutable(tmp_wiki: Path):
    store = WikiStore(tmp_wiki)
    src = SourcePaper(
        id="papers/2406.12345",
        url="https://arxiv.org/abs/2406.12345",
        title="A paper",
        ingested_at=date(2026, 6, 4),
        ingested_by="x",
        checksum="sha256:abc",
        body="original",
    )
    store.write_source(src)
    src2 = SourcePaper(**{**src.__dict__, "body": "tampered"})
    with pytest.raises(FileExistsError):
        store.write_source(src2)


def test_write_and_overwrite_page(tmp_wiki: Path):
    store = WikiStore(tmp_wiki)
    card = PageCard(
        id="tech-x",
        type="technique",
        status="scratch",
        title="x",
        tags=[],
        sources=[],
        related_runs=[],
        related_projects=[],
        revisit_after=None,
        created_at=date(2026, 6, 4),
        last_reviewed_at=date(2026, 6, 4),
        reviewer_note="",
        body="v1",
    )
    store.write_page(card)
    card2 = PageCard(**{**card.__dict__, "status": "candidate", "body": "v2"})
    store.write_page(card2)  # pages ARE mutable -- promotion edits frontmatter
    loaded = store.read_page("technique", "tech-x")
    assert loaded.status == "candidate"
    assert loaded.body == "v2"


def test_iter_pages_infers_missing_structural_metadata(tmp_wiki: Path):
    root_page = tmp_wiki / "pages" / "gpu-cnn-invariance.md"
    root_page.write_text(
        "---\n"
        "sources: []\n"
        "---\n\n"
        "# GPU CNN invariance\n\n"
        "Measured project evidence.\n"
    )
    nested_page = tmp_wiki / "pages" / "techniques" / "loop-fusion.md"
    nested_page.write_text(
        "---\n"
        "sources: []\n"
        "---\n\n"
        "# Feature extraction loop fusion\n"
    )

    cards = {card.id: card for card in WikiStore(tmp_wiki).iter_pages()}

    assert cards["gpu-cnn-invariance"].type == "fact"
    assert cards["gpu-cnn-invariance"].status == "scratch"
    assert cards["gpu-cnn-invariance"].title == "GPU CNN invariance"
    assert cards["loop-fusion"].type == "technique"
    assert cards["loop-fusion"].title == "Feature extraction loop fusion"


def test_write_and_iter_note_sources(tmp_wiki: Path):
    store = WikiStore(tmp_wiki)
    note = SourceNote(
        id="notes/2026-06-06-note",
        title="note",
        mission_id="m1",
        created_at=date(2026, 6, 6),
        tags=["ops"],
        body="body",
    )
    path = store.write_source(note)
    assert path == tmp_wiki / "sources" / "notes" / "2026-06-06-note.md"
    assert store.iter_note_sources() == [note]


def test_note_id_validated(tmp_wiki: Path):
    store = WikiStore(tmp_wiki)
    note = SourceNote(
        id="notes/../../evil",
        title="bad",
        mission_id="m1",
        created_at=date(2026, 6, 6),
        tags=[],
        body="body",
    )
    with pytest.raises(ValueError):
        store.write_source(note)
