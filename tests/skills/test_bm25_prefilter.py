"""Tests for argus_skill.skills.bm25_prefilter — optional matcher prefilter."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from argus_skill.skills.bm25_prefilter import (
    bm25_prefilter,
    is_prefilter_enabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("ARGUS_SKILL_BM25_PREFILTER_THRESHOLD", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_BM25_PREFILTER_TOPK", raising=False)
    yield


def _summary(name: str, desc: str, category: str = "") -> dict:
    return {"name": name, "description": desc, "category": category}


def test_prefilter_disabled_for_small_pool():
    # ≤ default threshold 40 → disabled (small bootstrap/test stores stay LLM-only)
    assert not is_prefilter_enabled(40)
    assert not is_prefilter_enabled(30)


def test_prefilter_enabled_above_threshold():
    # real role pools (~75-80) now activate the prefilter
    assert is_prefilter_enabled(41)
    assert is_prefilter_enabled(80)


def test_threshold_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_BM25_PREFILTER_THRESHOLD", "30")
    assert not is_prefilter_enabled(30)
    assert is_prefilter_enabled(31)


def test_threshold_zero_forces_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_BM25_PREFILTER_THRESHOLD", "0")
    assert is_prefilter_enabled(1)
    assert not is_prefilter_enabled(0)  # empty pool is still empty


def test_invalid_threshold_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_BM25_PREFILTER_THRESHOLD", "not-a-number")
    assert not is_prefilter_enabled(40)
    assert is_prefilter_enabled(41)


def test_default_topk_is_30(monkeypatch):
    # No top_k arg + no env → the default top_k=30 narrows a >30 pool to 30.
    monkeypatch.delenv("ARGUS_SKILL_BM25_PREFILTER_TOPK", raising=False)
    pool = [_summary(f"s{i}", "x y z") for i in range(50)]
    out = bm25_prefilter("task", pool)
    assert len(out) == 30


def test_bm25_returns_full_list_when_topk_ge_n():
    pool = [_summary("a", "x"), _summary("b", "y")]
    out = bm25_prefilter("anything", pool, top_k=10)
    assert out == pool


def test_bm25_returns_full_list_when_no_topk_set(monkeypatch):
    pool = [_summary(f"s{i}", "x y z") for i in range(5)]
    out = bm25_prefilter("task", pool, top_k=20)
    assert len(out) == 5


def test_bm25_ranks_matching_skill_first():
    pool = [
        _summary("unrelated-one", "audio waveform processing"),
        _summary("matching", "vllm engine eager-mode skill for inference"),
        _summary("unrelated-two", "matplotlib figure rendering"),
        _summary("partial", "tensorflow inference notes"),
    ]
    out = bm25_prefilter("vllm eager mode inference", pool, top_k=2)
    assert len(out) == 2
    names = [s["name"] for s in out]
    assert "matching" in names
    assert "unrelated-one" not in names
    assert "matplotlib" not in " ".join(names)


def test_bm25_respects_topk_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARGUS_SKILL_BM25_PREFILTER_TOPK", "1")
    pool = [
        _summary("first", "alpha beta gamma"),
        _summary("second", "delta epsilon zeta"),
        _summary("third", "alpha beta gamma delta"),
    ]
    out = bm25_prefilter("alpha beta", pool)
    assert len(out) == 1


def test_bm25_empty_query_returns_topk_of_pool():
    pool = [_summary(f"s{i}", "x") for i in range(5)]
    out = bm25_prefilter("", pool, top_k=3)
    assert len(out) == 3


def test_bm25_empty_pool_returns_empty():
    assert bm25_prefilter("task", []) == []


def test_bm25_handles_skill_with_history():
    pool = [
        {
            "name": "lookup",
            "description": "search code",
            "category": "research",
            "task_history": [
                "Find vLLM compatibility issues in lmms-eval",
                "audit benchmark seed shuffling",
            ],
        },
        _summary("plain", "unrelated topic"),
    ]
    out = bm25_prefilter("seed shuffling audit", pool, top_k=1)
    assert out[0]["name"] == "lookup"


def test_bm25_fallback_on_corrupt_summary():
    # Force a broken summary that would raise on .get if not handled gracefully.
    class Broken:
        def get(self, *args, **kw):
            raise RuntimeError("synthetic failure")

    pool = [Broken(), _summary("ok", "alpha")]
    # Should not raise — falls back to returning the input list.
    out = bm25_prefilter("alpha", pool, top_k=1)
    assert out  # returned something rather than blowing up
