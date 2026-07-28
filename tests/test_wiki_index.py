from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from argus_skill.wiki.index import rebuild_indexes
from argus_skill.wiki.schema import PageCard
from argus_skill.wiki.store import WikiStore


@pytest.fixture
def wiki(tmp_path: Path) -> WikiStore:
    root = tmp_path / ".autors" / "demo" / "wiki"
    for sub in (
        "sources/papers",
        "pages/techniques",
        "pages/conflicts",
        "pages/patterns",
        "queries",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return WikiStore(root)


def _make_card(**over) -> PageCard:
    base = dict(
        id="x",
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
        body="",
    )
    base.update(over)
    return PageCard(**base)


def test_rebuild_writes_four_indexes(wiki: WikiStore):
    wiki.write_page(_make_card(id="t1", status="candidate", tags=["grpo"]))
    wiki.write_page(_make_card(id="t2", status="stable", tags=["grpo", "ppo"]))
    wiki.write_page(_make_card(id="c1", type="conflict", status="candidate"))
    rebuild_indexes(wiki, today=date(2026, 6, 4))
    for name in (
        "by-status.md",
        "by-tag.md",
        "stale-watchlist.md",
        "open-contradictions.md",
    ):
        assert (wiki.root / "queries" / name).exists()


def test_by_status_lists_each_card(wiki: WikiStore):
    wiki.write_page(_make_card(id="t1", status="candidate"))
    wiki.write_page(_make_card(id="t2", status="stable"))
    rebuild_indexes(wiki, today=date(2026, 6, 4))
    body = (wiki.root / "queries" / "by-status.md").read_text()
    assert "t1" in body and "t2" in body
    assert "candidate" in body and "stable" in body


def test_stale_watchlist_filters_by_revisit_after(wiki: WikiStore):
    wiki.write_page(
        _make_card(
            id="overdue",
            status="candidate",
            revisit_after=date(2026, 3, 1),
        )
    )
    wiki.write_page(
        _make_card(
            id="fresh",
            status="candidate",
            revisit_after=date(2026, 12, 1),
        )
    )
    rebuild_indexes(wiki, today=date(2026, 6, 4))
    body = (wiki.root / "queries" / "stale-watchlist.md").read_text()
    assert "overdue" in body
    assert "fresh" not in body


def test_open_contradictions_lists_conflict_cards_without_resolution(wiki: WikiStore):
    wiki.write_page(
        _make_card(
            id="open-c",
            type="conflict",
            status="candidate",
        )
    )
    rebuild_indexes(wiki, today=date(2026, 6, 4))
    body = (wiki.root / "queries" / "open-contradictions.md").read_text()
    assert "open-c" in body


def test_index_rebuild_atomic_on_failure(wiki: WikiStore, monkeypatch: pytest.MonkeyPatch):
    qroot = wiki.root / "queries"
    for name in ("by-status.md", "by-tag.md", "stale-watchlist.md", "open-contradictions.md"):
        (qroot / name).write_text(f"OLD {name}\n", encoding="utf-8")

    import argus_skill.wiki.index as wiki_index

    def boom(_pages):
        raise RuntimeError("render failed")

    monkeypatch.setattr(wiki_index, "_render_by_tag", boom)
    with pytest.raises(RuntimeError, match="render failed"):
        rebuild_indexes(wiki, today=date(2026, 6, 4))

    for name in ("by-status.md", "by-tag.md", "stale-watchlist.md", "open-contradictions.md"):
        assert (qroot / name).read_text(encoding="utf-8") == f"OLD {name}\n"
    assert list(wiki.root.glob("queries*.new-*")) == []
    assert list(qroot.glob("*.tmp-*")) == []


def test_rebuild_skips_one_malformed_direct_edit(wiki: WikiStore):
    wiki.write_page(_make_card(id="valid-concept", type="concept"))
    bad = wiki.root / "pages" / "concepts" / "broken.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("---\ntype: executable\n---\n\ninvalid explicit type\n")

    rebuild_indexes(wiki)

    body = (wiki.root / "queries" / "by-status.md").read_text()
    assert "valid-concept" in body
    assert "broken" not in body
