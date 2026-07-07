"""LLM-judged wiki-page independence check: ``WikiRouter.judge_runner`` — the
wiki-page counterpart to ``tests/skills/test_skill_llm_duplicate_judge.py``.

Independence (duplicate) detection is judged ENTIRELY by an LLM. There is no
lexical/scored fallback: when a non-empty wiki needs the duplicate judge,
missing/broken/unusable judge infrastructure now rejects the proposal
explicitly instead of silently letting it through.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.router import WikiRouter
from argus_skill.wiki.schema import PageCard
from argus_skill.wiki.store import WikiStore


def _seed_page(store: WikiStore, *, id: str = "existing-page",
                title: str = "Existing Page", body: str = "Existing knowledge.") -> None:
    store.write_page(PageCard(
        id=id, type="technique", status="scratch", title=title, tags=[],
        sources=[], related_runs=[], related_projects=[], revisit_after=None,
        created_at=date(2026, 7, 4), last_reviewed_at=date(2026, 7, 4),
        reviewer_note="", body=body,
    ))


def test_llm_judge_rejects_when_model_says_duplicate(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root))
    backend = MemoryBackend()
    backend.queue("wiki.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": True, "of": "Existing Page", "why": "same underlying fact",
    })))
    router = WikiRouter(root, judge_runner=backend, judge_model="m")

    events: list[dict] = []
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "totally-different-slug",
        "title": "Completely different wording", "body": "unrelated-sounding text",
    }], on_event=events.append)
    assert counts["created"] == 0 and counts["rejected"] == 1
    rejected = [e for e in events if e.get("type") == "wiki.op.rejected"]
    assert rejected and "llm judge" in rejected[0]["text"]
    assert len(WikiStore(root).iter_pages()) == 1


def test_llm_judge_allows_when_model_says_not_duplicate(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root))
    backend = MemoryBackend()
    backend.queue("wiki.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": False, "of": "", "why": "different technique",
    })))
    router = WikiRouter(root, judge_runner=backend, judge_model="m")

    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "genuinely-new-page",
        "title": "Genuinely new technique", "body": "new knowledge",
    }])
    assert counts["created"] == 1
    assert len(WikiStore(root).iter_pages()) == 2


def test_llm_judge_empty_wiki_short_circuits_without_calling_runner(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)

    class _ExplodingRunner:
        def run_exec(self, **_kwargs):  # pragma: no cover - must never run
            raise AssertionError("judge runner must not be called against an empty wiki")

    router = WikiRouter(root, judge_runner=_ExplodingRunner(), judge_model="m")
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "first-page",
        "title": "First page", "body": "first knowledge",
    }])
    assert counts["created"] == 1


def test_no_judge_runner_rejects_when_wiki_is_nonempty(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root), title="GRPO Asymmetric Clipping",
               body="GRPO clips the ratio asymmetrically to keep training stable.")
    router = WikiRouter(root)  # no judge_runner

    events: list[dict] = []
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "near-dup-slug",
        "title": "Asymmetric Ratio Clipping for GRPO",
        "body": "To keep GRPO training stable, clip the ratio asymmetrically.",
    }], on_event=events.append)
    assert counts["created"] == 0 and counts["rejected"] == 1
    rejected = [e for e in events if e.get("type") == "wiki.op.rejected"]
    assert rejected and "duplicate judge unavailable" in rejected[0]["text"]
    assert len(WikiStore(root).iter_pages()) == 1


def test_judge_runner_exception_rejects_proposal(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root), title="GRPO Asymmetric Clipping",
               body="GRPO clips the ratio asymmetrically to keep training stable.")

    class _BrokenRunner:
        def run_exec(self, **_kwargs):
            raise RuntimeError("backend exploded")

    router = WikiRouter(root, judge_runner=_BrokenRunner(), judge_model="m")
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "near-dup-slug",
        "title": "Asymmetric Ratio Clipping for GRPO",
        "body": "To keep GRPO training stable, clip the ratio asymmetrically.",
    }])
    assert counts["created"] == 0 and counts["rejected"] == 1


def test_judge_malformed_json_rejects_proposal(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root), title="GRPO Asymmetric Clipping",
               body="GRPO clips the ratio asymmetrically to keep training stable.")
    backend = MemoryBackend()
    backend.queue("wiki.duplicate_check", CannedResponse(message="not valid json"))
    router = WikiRouter(root, judge_runner=backend, judge_model="m")
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "near-dup-slug",
        "title": "Asymmetric Ratio Clipping for GRPO",
        "body": "To keep GRPO training stable, clip the ratio asymmetrically.",
    }])
    assert counts["created"] == 0 and counts["rejected"] == 1


def test_judge_duplicate_without_target_rejects_proposal(tmp_path: Path) -> None:
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root), title="Debug CUDA OOM",
               body="Check nvidia-smi, reduce batch size, enable gradient checkpointing.")
    backend = MemoryBackend()
    backend.queue("wiki.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": True, "of": "", "why": "vague",
    })))
    router = WikiRouter(root, judge_runner=backend, judge_model="m")
    counts = router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "rest-api-testing",
        "title": "Write unit tests for a REST endpoint",
        "body": "Spin up a test client, assert status codes and response bodies.",
    }])
    assert counts["created"] == 0 and counts["rejected"] == 1


def test_update_page_is_still_exempt_with_judge_runner_configured(tmp_path: Path) -> None:
    """A revision of the SAME id must never even reach the judge — mirrors
    the mechanical path's exemption for ``update_page``/pre-existing ids."""
    root = init_wiki("demo", base=tmp_path)
    _seed_page(WikiStore(root), id="p", title="P", body="v1")

    class _ExplodingRunner:
        def run_exec(self, **_kwargs):  # pragma: no cover - must never run
            raise AssertionError("judge must not be consulted for an update to an existing id")

    router = WikiRouter(root, judge_runner=_ExplodingRunner(), judge_model="m")
    counts = router.apply_ops([{
        "op": "update_page", "card_type": "technique", "id": "p",
        "title": "P", "body": "v2",
    }])
    assert counts["updated"] == 1


def test_judge_prompt_uses_short_excerpt_not_full_body(tmp_path: Path) -> None:
    """Cost-control regression: the judge prompt must show a SHORT excerpt of
    each existing page's body, never the full text — otherwise a long wiki
    page blows up the prompt for every subsequent proposal."""
    root = init_wiki("demo", base=tmp_path)
    long_body_marker = "LONG PAGE BODY MARKER " * 100
    _seed_page(WikiStore(root), title="Existing Page", body=long_body_marker)
    captured: dict[str, str] = {}

    class _CapturingRunner:
        def run_exec(self, *, prompt, **_kwargs):
            captured["prompt"] = prompt
            raise RuntimeError("stop after capturing — no real call needed")

    router = WikiRouter(root, judge_runner=_CapturingRunner(), judge_model="m")
    router.apply_ops([{
        "op": "create_page", "card_type": "technique", "id": "new-page",
        "title": "New Page", "body": "new content",
    }])
    assert "prompt" in captured
    assert long_body_marker not in captured["prompt"]
    assert "Existing Page" in captured["prompt"]
    assert "LONG PAGE BODY MARKER" in captured["prompt"]  # excerpt present, just truncated
