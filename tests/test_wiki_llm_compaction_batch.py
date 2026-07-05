"""LLM-judged periodic wiki compaction: ``wiki.compaction.llm_cluster_wiki``.

The batched-clustering counterpart to ``tests/test_wiki_llm_duplicate_judge.py``
(create-time check). Here the LLM only decides WHICH pages group together in
one call per batch; the harness ALWAYS decides which one in each group to
keep via ``_representative`` — a mechanical pick from real maturity data
(``status``/``sources``) the model never sees. There is no lexical/scored
fallback: a judge failure (no runner, exception, malformed response) simply
returns ``None`` — "nothing to do this round" — never a mechanical
clustering pass.
"""
from __future__ import annotations

import json
from datetime import date

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.wiki.compaction import llm_cluster_wiki
from argus_skill.wiki.schema import PageCard


def _page(id: str, title: str, body: str, *, card_type: str = "technique",
          status: str = "scratch", sources: list[str] | None = None) -> PageCard:
    return PageCard(
        id=id, type=card_type, status=status, title=title, tags=[],
        sources=sources or [], related_runs=[], related_projects=[],
        revisit_after=None, created_at=date(2026, 7, 4),
        last_reviewed_at=date(2026, 7, 4), reviewer_note="", body=body,
    )


def test_llm_groups_paraphrased_pages_by_title(tmp_path) -> None:
    a = _page("a", "GRPO Asymmetric Clipping",
              "GRPO clips the ratio asymmetrically to keep training stable.",
              status="scratch")
    b = _page("b", "Asymmetric Ratio Clipping for GRPO",
              "To keep GRPO training stable, clip the ratio asymmetrically.",
              status="candidate", sources=["s1"])
    c = _page("c", "KV-cache paging",
              "Paged attention splits the KV cache into fixed-size blocks.")
    backend = MemoryBackend()
    backend.queue("wiki.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["GRPO Asymmetric Clipping", "Asymmetric Ratio Clipping for GRPO"]],
    })))
    clusters = llm_cluster_wiki([a, b, c], judge_runner=backend, judge_model="m")
    assert clusters is not None
    assert len(clusters) == 1
    assert {p.id for p in clusters[0]} == {"a", "b"}


def test_llm_finds_no_clusters_returns_empty_not_none() -> None:
    a = _page("a", "GRPO Asymmetric Clipping", "GRPO clips the ratio asymmetrically.")
    b = _page("b", "KV-cache paging", "Paged attention splits the KV cache into blocks.")
    backend = MemoryBackend()
    backend.queue("wiki.compaction_batch", CannedResponse(message=json.dumps({"clusters": []})))
    clusters = llm_cluster_wiki([a, b], judge_runner=backend, judge_model="m")
    assert clusters == []


def test_llm_hallucinated_titles_drop_the_group() -> None:
    a = _page("a", "Real Page One", "Some real content.")
    b = _page("b", "Real Page Two", "Some other real content.")
    backend = MemoryBackend()
    backend.queue("wiki.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["Ghost Page", "Also Ghost"]],
    })))
    clusters = llm_cluster_wiki([a, b], judge_runner=backend, judge_model="m")
    assert clusters == []


def test_malformed_json_returns_none(tmp_path) -> None:
    a = _page("a", "A", "body a")
    b = _page("b", "B", "body b")
    backend = MemoryBackend()
    backend.queue("wiki.compaction_batch", CannedResponse(message="not valid json"))
    assert llm_cluster_wiki([a, b], judge_runner=backend, judge_model="m") is None


def test_no_judge_runner_returns_none() -> None:
    a = _page("a", "A", "body a")
    b = _page("b", "B", "body b")
    assert llm_cluster_wiki([a, b], judge_runner=None) is None


def test_judge_runner_exception_returns_none(tmp_path) -> None:
    a = _page("a", "A", "body a")
    b = _page("b", "B", "body b")

    class _BrokenRunner:
        def run_exec(self, **_kwargs):
            raise RuntimeError("backend exploded")

    assert llm_cluster_wiki([a, b], judge_runner=_BrokenRunner(), judge_model="m") is None


def test_a_grouping_defense_in_depth_same_card_type_control() -> None:
    """Grouping trust now lives entirely in the prompt's instruction ("pages
    of a DIFFERENT card_type... can NEVER group together") plus the judge's
    own reasoning — there is no code-level score/threshold guard anymore.
    This pins the honest control case: a same-card_type near-duplicate pair
    the judge groups is trusted and returned as a cluster."""
    a = _page("a", "Batch size tuning",
              "Larger batch size improves throughput up to a point.",
              card_type="technique")
    b = _page("b", "Batch size tuning revisited",
              "Larger batch size improves throughput up to a point in practice.",
              card_type="technique")
    backend = MemoryBackend()
    backend.queue("wiki.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["Batch size tuning", "Batch size tuning revisited"]],
    })))
    clusters = llm_cluster_wiki([a, b], judge_runner=backend, judge_model="m")
    assert clusters is not None and len(clusters) == 1


def test_a_page_cannot_be_claimed_by_two_groups() -> None:
    a = _page("a", "GRPO Asymmetric Clipping",
              "GRPO clips the ratio asymmetrically to keep training stable.")
    b = _page("b", "Asymmetric Ratio Clipping for GRPO",
              "To keep GRPO training stable, clip the ratio asymmetrically.")
    c = _page("c", "GRPO clip variant",
              "Another way to keep GRPO training stable via asymmetric clipping.")
    backend = MemoryBackend()
    backend.queue("wiki.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [
            ["GRPO Asymmetric Clipping", "Asymmetric Ratio Clipping for GRPO"],
            ["Asymmetric Ratio Clipping for GRPO", "GRPO clip variant"],
        ],
    })))
    clusters = llm_cluster_wiki([a, b, c], judge_runner=backend, judge_model="m")
    assert clusters is not None
    assert len(clusters) == 1
    assert {p.id for p in clusters[0]} == {"a", "b"}
