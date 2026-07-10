"""Reviewer-proposed project wiki memory: wiki_ops -> WikiRouter.

The wiki's structured counterpart to ``skill_ops`` -> ``SkillRouter`` (see
``tests/test_loop_failure_lesson.py``), wired the same way and with the same
"no Manager gate" contract -- the reviewer is the sole authority:

* The reviewer never mutates the project wiki directly. It emits ``wiki_ops``
  in its verdict -- ``create_page``/``update_page`` PROPOSALS (each carrying
  page markdown + cited evidence spans) and ``retire_page`` requests.
* ``WikiRouter`` owns the write path. A create/update must clear the
  anti-fabrication evidence-verbatim check (every cited quote must appear,
  verbatim, in an already-ingested immutable wiki source); ``retire_page`` is
  always a tombstone, never a hard delete.
* This is a no-op end to end whenever the project has no initialized wiki
  (``discover_wikis`` finds nothing under ``<workdir>/.autors/*/wiki``) --
  ordinary (non-``learning``) missions are unaffected.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.core.models import ReviewDecision, RoundRecord
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.schema import SourceNote
from argus_skill.wiki.store import WikiStore

_MATERIAL = "The GRPO trick clips the ratio asymmetrically for training stability."


def _init_wiki_with_source(workdir: Path) -> Path:
    """Bootstrap a wiki under ``<workdir>/.autors/demo/wiki`` with one
    immutable source the reviewer can cite evidence against. ``init_wiki``
    writes to ``base/.autors/<project>/wiki``, so passing ``workdir`` itself
    as base means ``discover_wikis(workdir)`` will find it directly."""
    root = init_wiki("demo", base=workdir)
    WikiStore(root).write_source(SourceNote(
        id="grpo-tricks", title="GRPO tricks", mission_id="m1",
        created_at=date(2026, 7, 4), tags=[], body=_MATERIAL,
    ))
    return root


def _review_with_wiki_ops(*, status: str = "done", wiki_ops: list[dict]) -> str:
    return json.dumps({
        "status": status,
        "reason": "verdict with wiki ops",
        "next_action": "none" if status == "done" else "carry the lesson forward",
        "round_summary_markdown": "# Review\n\n- proposed wiki ops\n",
        "completion_summary_markdown": "",
        "wiki_ops": wiki_ops,
    })


def _loop(skills_dir: Path, backend: MemoryBackend, events: list,
          *, enabled: bool = True) -> SkillLoop:
    return SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, wiki_ops_enabled=enabled),
        on_event=events.append,
    )


def _queue_no_op_distill(backend: MemoryBackend) -> None:
    """Suppress the Scientist auto-distill path (fires on ``"matched": []``,
    independent of wiki_ops) so it cannot pollute these tests with an
    unrelated skill-store side effect."""
    backend.queue("scientist.skill_distill", CannedResponse(message="NONE"))


def test_create_page_applied_with_valid_evidence(tmp_path: Path) -> None:
    wiki_root = _init_wiki_with_source(tmp_path)
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="Learned the GRPO trick."))
    backend.queue("reviewer", CannedResponse(message=_review_with_wiki_ops(wiki_ops=[{
        "op": "create_page", "id": "grpo-async-clip", "card_type": "technique",
        "title": "Async clip", "status": "scratch",
        "body": "GRPO clips the ratio asymmetrically.",
        "evidence": [{"source_id": "grpo-tricks",
                      "quote": "clips the ratio asymmetrically"}],
        "why": "reusable technique from this mission's material",
    }])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("learn from the material", workdir=tmp_path)

    assert any(e.get("type") == "wiki.created" for e in events), [
        e.get("type") for e in events]
    page = wiki_root / "pages" / "techniques" / "grpo-async-clip.md"
    assert page.exists()
    assert "clips the ratio asymmetrically" in page.read_text(encoding="utf-8")


def test_create_page_rejected_when_evidence_fabricated(tmp_path: Path) -> None:
    wiki_root = _init_wiki_with_source(tmp_path)
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(message=_review_with_wiki_ops(wiki_ops=[{
        "op": "create_page", "id": "fake-page", "card_type": "technique",
        "title": "Fake", "body": "a claim",
        "evidence": [{"source_id": "grpo-tricks", "quote": "a line that is not there"}],
        "why": "x",
    }])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("learn from the material", workdir=tmp_path)

    assert any(e.get("type") == "wiki.op.rejected" for e in events), [
        e.get("type") for e in events]
    assert not (wiki_root / "pages" / "techniques" / "fake-page.md").exists()


def test_retire_page_tombstones_existing_page(tmp_path: Path) -> None:
    wiki_root = _init_wiki_with_source(tmp_path)
    from argus_skill.wiki.router import WikiRouter
    # Seed an existing page directly (as an earlier mission would have).
    WikiRouter(wiki_root).apply_ops([{
        "op": "create_page", "id": "stale-page", "card_type": "technique",
        "title": "Stale", "body": "outdated info",
    }])
    assert (wiki_root / "pages" / "techniques" / "stale-page.md").exists()

    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(message=_review_with_wiki_ops(wiki_ops=[{
        "op": "retire_page", "id": "stale-page", "card_type": "technique",
        "why": "superseded by the new material",
    }])))

    events: list[dict] = []
    _loop(skills_dir, backend, events).run("learn from the material", workdir=tmp_path)

    assert any(e.get("type") == "wiki.retired" for e in events), [
        e.get("type") for e in events]
    assert not (wiki_root / "pages" / "techniques" / "stale-page.md").exists()


def test_wiki_ops_ignored_when_disabled(tmp_path: Path) -> None:
    wiki_root = _init_wiki_with_source(tmp_path)
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(message=_review_with_wiki_ops(wiki_ops=[{
        "op": "create_page", "id": "should-not-exist", "card_type": "technique",
        "title": "Nope", "body": "irrelevant",
        "evidence": [{"source_id": "grpo-tricks", "quote": "clips the ratio asymmetrically"}],
    }])))

    events: list[dict] = []
    _loop(skills_dir, backend, events, enabled=False).run(
        "learn from the material", workdir=tmp_path)

    assert not any(e.get("type", "").startswith("wiki.") for e in events)
    assert not (wiki_root / "pages" / "techniques" / "should-not-exist.md").exists()


def test_wiki_ops_are_a_noop_when_no_wiki_initialized(tmp_path: Path) -> None:
    """No ``.autors/*/wiki`` under workdir at all -- ``discover_wikis`` finds
    nothing, so a proposed wiki_op is silently dropped (never an error), same
    as any ordinary non-``learning`` mission today."""
    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    _queue_no_op_distill(backend)
    backend.queue("engineer-r1", CannedResponse(message="ran"))
    backend.queue("reviewer", CannedResponse(message=_review_with_wiki_ops(wiki_ops=[{
        "op": "create_page", "id": "no-wiki-here", "card_type": "technique",
        "title": "N/A", "body": "irrelevant",
    }])))

    events: list[dict] = []
    outcome = _loop(skills_dir, backend, events).run(
        "learn from the material", workdir=tmp_path)

    assert outcome.status == "done"
    assert not any(e.get("type", "").startswith("wiki.") for e in events)
    assert not (tmp_path / ".autors").exists()


def _round(review_wiki_ops: list[dict]) -> RoundRecord:
    return RoundRecord(
        round_index=1, engineer_message="", engineer_exit_code=0,
        review=ReviewDecision(
            status="continue", reason="r", next_action="n", wiki_ops=review_wiki_ops,
        ),
    )


def test_collect_wiki_ops_dedups_identical_repeats_across_rounds() -> None:
    """Mirrors ``_collect_skill_ops``'s de-dup: the reviewer may repeat the
    SAME create_page proposal round after round while the engineer keeps
    working; only the first occurrence should survive to be applied."""
    op = {"op": "create_page", "id": "p", "body": "same body"}
    rounds = [_round([op]), _round([dict(op)])]
    ops = SkillLoop._collect_wiki_ops(rounds)
    assert len(ops) == 1


def test_collect_wiki_ops_keeps_distinct_ops() -> None:
    rounds = [_round([
        {"op": "create_page", "id": "a", "body": "body a"},
        {"op": "create_page", "id": "b", "body": "body b"},
        {"op": "retire_page", "id": "c", "why": "x"},
    ])]
    ops = SkillLoop._collect_wiki_ops(rounds)
    assert len(ops) == 3


def test_collect_wiki_ops_empty_when_no_rounds_or_no_ops() -> None:
    assert SkillLoop._collect_wiki_ops([]) == []
    assert SkillLoop._collect_wiki_ops([_round([])]) == []
