from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event, Thread

import pytest

from argus_skill.wiki.schema import PageCard, SourcePaper
from argus_skill.wiki.store import WikiStore


def _wiki_root(tmp_path: Path) -> Path:
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


def _paper(paper_id: str = "papers/2406.12345", *, body: str = "abstract") -> SourcePaper:
    return SourcePaper(
        id=paper_id,
        url="https://arxiv.org/abs/2406.12345",
        title="A paper",
        ingested_at=date(2026, 6, 4),
        ingested_by="test",
        checksum="sha256:abc",
        body=body,
    )


def _card(card_id: str = "tech-x", *, body: str = "") -> PageCard:
    return PageCard(
        id=card_id,
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
        body=body,
    )


def _concurrent_source_worker(root: str, body: str) -> str:
    try:
        WikiStore(Path(root)).write_source(_paper(body=body))
        return "ok"
    except FileExistsError:
        return "exists"


def test_write_source_rejects_path_traversal(tmp_path: Path):
    root = _wiki_root(tmp_path)
    store = WikiStore(root)
    with pytest.raises(ValueError):
        store.write_source(_paper("papers/../../x"))
    assert not (root / "x.md").exists()
    assert list((root / "sources" / "papers").glob("*.md")) == []


def test_write_page_rejects_path_traversal(tmp_path: Path):
    root = _wiki_root(tmp_path)
    store = WikiStore(root)
    with pytest.raises(ValueError):
        store.write_page(_card("../../evil"))
    assert not (root / "pages" / "evil.md").exists()
    assert list((root / "pages" / "techniques").glob("*.md")) == []


def test_write_source_normalizes_legacy_arxiv_slash(tmp_path: Path):
    root = _wiki_root(tmp_path)
    store = WikiStore(root)
    src = _paper("papers/cs/0112017")
    path = store.write_source(src)
    assert path == root / "sources" / "papers" / "cs__0112017.md"
    assert store.read_source(SourcePaper, "papers/cs/0112017") == src


def test_concurrent_same_source_write_is_safe(tmp_path: Path):
    root = _wiki_root(tmp_path)
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_concurrent_source_worker, [str(root), str(root)], ["a", "b"]))
    assert sorted(results) == ["exists", "ok"]
    loaded = WikiStore(root).read_source(SourcePaper, "papers/2406.12345")
    assert loaded.body in {"a", "b"}


def test_concurrent_page_write_and_read(tmp_path: Path):
    root = _wiki_root(tmp_path)
    store = WikiStore(root)
    store.write_page(_card(body="initial"))
    stop = Event()
    errors: list[Exception] = []

    def writer() -> None:
        for idx in range(50):
            try:
                store.write_page(_card(body=f"body-{idx}"))
            except Exception as exc:  # pragma: no cover - captured for assertion
                errors.append(exc)
        stop.set()

    def reader() -> None:
        while not stop.is_set():
            try:
                pages = store.iter_pages()
                assert len(pages) == 1
                assert pages[0].id == "tech-x"
            except Exception as exc:  # pragma: no cover - captured for assertion
                errors.append(exc)
                stop.set()

    t1 = Thread(target=writer)
    t2 = Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    stop.set()
    t2.join()
    assert errors == []
