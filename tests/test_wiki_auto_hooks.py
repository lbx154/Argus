from __future__ import annotations

from datetime import date
from pathlib import Path

from argus_skill.wiki.auto_hooks import discover_wikis, prepare_wikis_for_review
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.lifecycle import maintain_wikis_after_mission
from argus_skill.wiki.schema import PageCard
from argus_skill.wiki.store import WikiStore

SAMPLE_BIB = """
@article{vaswani2017attention,
  title={Attention Is All You Need},
  author={Vaswani, A.},
  year={2017},
  url={https://arxiv.org/abs/1706.03762}
}
""".strip()


def test_prepare_imports_sources_without_creating_pages_or_run_history(
    tmp_path: Path,
) -> None:
    wiki = init_wiki("demo", base=tmp_path)
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")

    summary = prepare_wikis_for_review(tmp_path, mission_id="m1")

    assert summary[str(wiki)]["sources_written"] == 1
    assert list((wiki / "sources" / "papers").glob("*.md"))
    assert not list((wiki / "pages").glob("*/*.md"))
    assert not (wiki / "sources" / "runs").exists()


def test_prepare_is_idempotent(tmp_path: Path) -> None:
    wiki = init_wiki("demo", base=tmp_path)
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")

    prepare_wikis_for_review(tmp_path, mission_id="m1")
    second = prepare_wikis_for_review(tmp_path, mission_id="m2")

    assert second[str(wiki)]["sources_written"] == 0


def test_direct_knowledge_page_is_indexed_after_mission(tmp_path: Path) -> None:
    wiki = init_wiki("demo", base=tmp_path)
    store = WikiStore(wiki)
    store.write_page(PageCard(
        id="transformer-architecture",
        type="concept",
        status="candidate",
        title="Transformer architecture",
        tags=["transformer"],
        sources=[],
        related_runs=[],
        related_projects=[],
        revisit_after=None,
        created_at=date(2026, 7, 28),
        last_reviewed_at=date(2026, 7, 28),
        reviewer_note="",
        body="Self-attention and feed-forward blocks form the core stack.",
    ))

    summary = maintain_wikis_after_mission(
        workdir=tmp_path,
        auto_compact_enabled=False,
        reviewer_runner=None,
        reviewer_model="",
        reviewer_reasoning_effort="high",
    )

    assert summary["wiki_count"] == 1
    by_status = (wiki / "queries" / "by-status.md").read_text(encoding="utf-8")
    assert "concept/transformer-architecture" in by_status


def test_discover_skips_uninitialized_tree(tmp_path: Path) -> None:
    (tmp_path / ".autors" / "partial" / "wiki").mkdir(parents=True)
    assert discover_wikis(tmp_path) == []
