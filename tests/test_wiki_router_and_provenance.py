"""Wiki structured CRUD: retire_page tombstone, evidence-span provenance, and
the WikiRouter (symmetric to SkillRouter). All off the daemon hot path.
"""
from __future__ import annotations

from datetime import date

import pytest

from argus_skill.skills.provenance import verify_evidence
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.router import WikiRouter
from argus_skill.wiki.schema import PageCard, SourceNote, SourcePaper
from argus_skill.wiki.store import WikiStore

_MATERIAL = "The GRPO trick clips the ratio asymmetrically for training stability."


@pytest.fixture
def wiki_root(tmp_path):
    root = init_wiki("demo", base=tmp_path)
    WikiStore(root).write_source(SourceNote(
        id="grpo-tricks", title="GRPO tricks", mission_id="m1",
        created_at=date(2026, 7, 4), tags=[], body=_MATERIAL,
    ))
    return root


def _page(pid: str, body: str = "b") -> PageCard:
    return PageCard(
        id=pid, type="technique", status="scratch", title=pid.title(),
        tags=[], sources=[], related_runs=[], related_projects=[],
        revisit_after=None, created_at=date(2026, 7, 4),
        last_reviewed_at=date(2026, 7, 4), reviewer_note="", body=body,
    )


# --------------------------------------------------------------------------- #
# retire_page tombstone + iter_pages skip
# --------------------------------------------------------------------------- #
def test_retire_page_tombstones_and_disappears_from_iter(wiki_root):
    store = WikiStore(wiki_root)
    store.write_page(_page("t1"))
    assert any(p.id == "t1" for p in store.iter_pages())

    dest = store.retire_page("technique", "t1", reason="superseded", retired_by="reviewer")
    assert dest.exists() and "_retired" in dest.parts
    assert "RETIRED" in dest.read_text(encoding="utf-8")
    assert not (wiki_root / "pages" / "techniques" / "t1.md").exists()
    assert all(p.id != "t1" for p in store.iter_pages())


def test_retire_missing_page_raises(wiki_root):
    with pytest.raises(FileNotFoundError):
        WikiStore(wiki_root).retire_page("technique", "nope", reason="x", retired_by="r")


# --------------------------------------------------------------------------- #
# evidence-span provenance
# --------------------------------------------------------------------------- #
def test_evidence_verbatim_quote_passes(wiki_root):
    assert verify_evidence(
        [{"source_id": "grpo-tricks", "quote": "clips the ratio asymmetrically"}],
        wiki_root) == []


def test_evidence_fabricated_quote_flagged(wiki_root):
    problems = verify_evidence(
        [{"source_id": "grpo-tricks", "quote": "a claim that is simply not there"}],
        wiki_root)
    assert problems and "quote not found" in problems[0]


def test_evidence_missing_source_flagged(wiki_root):
    problems = verify_evidence([{"source_id": "ghost", "quote": "x"}], wiki_root)
    assert problems and "source not found" in problems[0]


def test_evidence_incomplete_span_flagged(wiki_root):
    assert verify_evidence([{"source_id": "grpo-tricks"}], wiki_root)  # no quote


# --------------------------------------------------------------------------- #
# WikiRouter structured ops
# --------------------------------------------------------------------------- #
def test_router_create_page_with_valid_evidence_and_reindex(wiki_root):
    router = WikiRouter(wiki_root)
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "grpo-async-clip",
        "status": "scratch", "title": "Async clip",
        "evidence": [{"source_id": "grpo-tricks", "quote": "clips the ratio asymmetrically"}],
        "body": "...", "why": "from material",
    }])
    assert counts["created"] == 1 and counts["rejected"] == 0
    by_status = (wiki_root / "queries" / "by-status.md").read_text(encoding="utf-8")
    assert "grpo-async-clip" in by_status


def test_router_rejects_fabricated_evidence(wiki_root):
    router = WikiRouter(wiki_root)
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "fake", "status": "scratch",
        "title": "Fake", "body": "...",
        "evidence": [{"source_id": "grpo-tricks", "quote": "totally fabricated line"}],
    }])
    assert counts["created"] == 0 and counts["rejected"] == 1
    assert not (wiki_root / "pages" / "techniques" / "fake.md").exists()


def test_router_update_then_retire(wiki_root):
    router = WikiRouter(wiki_root)
    ev = [{"source_id": "grpo-tricks", "quote": "asymmetrically"}]
    assert router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "p", "status": "scratch",
        "title": "P", "evidence": ev, "body": "v1"}])["created"] == 1
    assert router.apply_ops([{
        "op": "update_page", "card_type": "technique", "id": "p", "status": "scratch",
        "title": "P", "evidence": ev, "body": "v2"}])["updated"] == 1
    assert router.apply_ops([{
        "op": "retire_page", "card_type": "technique", "id": "p", "why": "stale"}])["retired"] == 1
    assert all(pg.id != "p" for pg in WikiStore(wiki_root).iter_pages())


def test_router_require_evidence_rejects_bare_page(wiki_root):
    router = WikiRouter(wiki_root)
    counts = router.apply_ops(
        [{"op": "create_page", "card_type": "technique", "id": "noev",
          "status": "scratch", "title": "NoEv", "body": "x"}],
        require_evidence=True)
    assert counts["created"] == 0 and counts["rejected"] == 1


def test_router_create_source_is_immutable(wiki_root):
    router = WikiRouter(wiki_root)
    assert router.apply_ops([{"op": "create_source", "id": "newnote",
                              "title": "N", "body": "hello"}])["sources"] == 1
    # re-ingesting the same id is a no-op (source layer is write-once)
    assert router.apply_ops([{"op": "create_source", "id": "newnote",
                              "title": "N", "body": "hello"}])["sources"] == 0


# --------------------------------------------------------------------------- #
# regression fixes from adversarial review
# --------------------------------------------------------------------------- #
def test_evidence_ambiguous_bare_stem_is_unresolved_but_prefix_resolves(tmp_path):
    root = init_wiki("demo", base=tmp_path)
    store = WikiStore(root)
    store.write_source(SourceNote(id="dup", title="n", mission_id="m",
                                  created_at=date(2026, 7, 4), tags=[],
                                  body="alpha lives in the note"))
    store.write_source(SourcePaper(id="dup", url="u", title="p",
                                   ingested_at=date(2026, 7, 4), ingested_by="x",
                                   checksum="c", body="beta lives in the paper"))
    # bare stem is ambiguous across sub-dirs -> refuse (do not silently pick one)
    probs = verify_evidence([{"source_id": "dup", "quote": "alpha lives in the note"}], root)
    assert probs and "source not found" in probs[0]
    # explicit type prefix resolves deterministically, each to its own file
    assert verify_evidence([{"source_id": "notes/dup", "quote": "alpha lives in the note"}], root) == []
    assert verify_evidence([{"source_id": "papers/dup", "quote": "beta lives in the paper"}], root) == []
    # a prefix pointing at the wrong file correctly fails
    assert verify_evidence([{"source_id": "papers/dup", "quote": "alpha lives in the note"}], root)


def test_retire_twice_keeps_both_tombstones(wiki_root):
    store = WikiStore(wiki_root)
    store.write_page(_page("p", body="FIRST-VERSION-BODY"))
    store.retire_page("technique", "p", reason="r1", retired_by="rev")
    store.write_page(_page("p", body="SECOND-VERSION-BODY"))
    store.retire_page("technique", "p", reason="r2", retired_by="rev")
    tombs = list((wiki_root / "pages" / "_retired" / "techniques").glob("*.md"))
    joined = "\n".join(t.read_text(encoding="utf-8") for t in tombs)
    assert len(tombs) == 2
    assert "FIRST-VERSION-BODY" in joined and "SECOND-VERSION-BODY" in joined


def test_iter_pages_ignores_ancestor_dir_named_retired(tmp_path):
    # a wiki checked out under a dir literally named _retired must still work
    root = init_wiki("demo", base=tmp_path / "_retired")
    store = WikiStore(root)
    store.write_page(_page("p"))
    assert any(pg.id == "p" for pg in store.iter_pages())


def test_router_create_source_reingest_counts_as_skipped(wiki_root):
    router = WikiRouter(wiki_root)
    r1 = router.apply_ops([{"op": "create_source", "id": "n2", "title": "N", "body": "hi"}])
    assert r1["sources"] == 1 and r1["skipped"] == 0
    r2 = router.apply_ops([{"op": "create_source", "id": "n2", "title": "N", "body": "hi"}])
    assert r2["sources"] == 0 and r2["skipped"] == 1 and r2["rejected"] == 0

