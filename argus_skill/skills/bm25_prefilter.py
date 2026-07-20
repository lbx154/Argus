"""Optional BM25 prefilter for the skill matcher.

When the skill pool is small (N <= 40 by default), the LLM matcher
itself is the cheapest precise selector: a single call to a small router
model (gpt-4o-mini / haiku-3.5) judges all candidates in one pass for
~$0.05 — see `loop.py:resolved_matcher_model` env note. No prefilter is
needed; adding one only burns latency.

When the pool grows past the threshold (the real argus role pools sit at
~75-80 candidates, each summary ~50 tokens ≈ 3.8k tokens just for the
listing), a cheap BM25 prefilter to top-K=30 narrows the candidate set
before the LLM matcher so the matcher prompt stays small while the LLM
still makes the final relevance call. This module implements that
prefilter as a clean hook that `SkillStore.find_relevant` calls before
shipping to the LLM.

Design notes
------------

* **No new runtime dependency.** Pure-stdlib BM25 (Okapi variant), based
  on the standard formulation; same result shape as `rank_bm25` so we
  can swap to that library later without changing call sites.
* **Unicode-aware tokens.** NFKC-normalized, lowercased ASCII alphanumeric
  tokens keep the historical ≥3-character rule; contiguous CJK text is split
  into overlapping character bigrams so Chinese/Japanese/Korean metadata can
  participate.
* **No lexical signal = no pruning.** An empty query or a cross-language query
  whose tokens occur in none of the candidate summaries returns the full pool to
  the LLM matcher. Arbitrary on-disk order must never decide recall.
* **Failure mode = fall back to LLM-only.** Any tokenization or
  index-building error → return the unfiltered candidate list and let
  the LLM matcher take over. Selection accuracy must never regress just
  because the prefilter tripped.
* **Threshold is env-tunable.** Default 40 makes the prefilter ACTIVE for
  the real role pools (~75-80) while small bootstrapping/test stores
  (<40) stay LLM-only (no recall change for them). Set
  ``ARGUS_SKILL_BM25_PREFILTER_THRESHOLD=0`` to force it on for debugging,
  ``ARGUS_SKILL_BM25_PREFILTER_TOPK`` to tune the post-prefilter size.

References
----------

* Robertson & Zaragoza (2009), "The Probabilistic Relevance Framework:
  BM25 and Beyond" — the canonical reference for the Okapi BM25 score.
* mem0 v3 (2026) — its "multi-signal retrieval" uses BM25 alongside
  semantic similarity for the read-time fusion; we use BM25 alone as a
  prefilter to feed the LLM (semantic similarity costs an embedding
  service we don't want as a hard dep).
"""
from __future__ import annotations

import logging
import math
import os
import re
import unicodedata
from collections import Counter
from typing import Sequence

log = logging.getLogger(__name__)

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+"
)
_DEFAULT_THRESHOLD = 40
_DEFAULT_TOP_K = 30

# BM25 hyperparameters — Okapi defaults from Robertson & Zaragoza (2009).
_K1 = 1.5
_B = 0.75


def bm25_tokens(text: str) -> list[str]:
    """Return the Unicode-aware lexical tokens used by BM25 retrieval paths."""
    if not text:
        return []
    lowered = unicodedata.normalize("NFKC", text).lower()
    lowered = lowered.replace("_", " ").replace("-", " ")
    tokens = [t for t in _ASCII_TOKEN_RE.findall(lowered) if len(t) >= 3]
    for match in _CJK_RUN_RE.finditer(lowered):
        run = match.group(0)
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _candidate_text(summary: dict) -> str:
    """Concatenate the matcher-visible fields of one skill summary."""
    parts = [
        str(summary.get("name", "")),
        str(summary.get("description", "")),
        str(summary.get("category", "")),
    ]
    hist = summary.get("task_history") or []
    if isinstance(hist, list):
        parts.append(" ".join(str(h) for h in hist[:3]))
    return " ".join(p for p in parts if p)


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("invalid env %s=%r — using default %d", name, raw, default)
        return default


def is_prefilter_enabled(n_candidates: int) -> bool:
    """Return True when BM25 prefilter should kick in for this pool size.

    Threshold tunable via ``ARGUS_SKILL_BM25_PREFILTER_THRESHOLD``.
    Setting it to 0 forces prefilter on for any non-empty pool.
    """
    threshold = _read_int_env(
        "ARGUS_SKILL_BM25_PREFILTER_THRESHOLD", _DEFAULT_THRESHOLD
    )
    if threshold <= 0:
        return n_candidates > 0
    return n_candidates > threshold


def bm25_prefilter(
    task_description: str,
    summaries: Sequence[dict],
    *,
    top_k: int | None = None,
) -> list[dict]:
    """Rank ``summaries`` by Okapi BM25 against ``task_description``.

    Returns at most ``top_k`` summaries when BM25 has lexical evidence. Empty or
    cross-language/no-overlap queries return the full pool for LLM-only matching.
    BM25 failures also return the full pool.
    """
    if not summaries:
        return []
    if top_k is None:
        top_k = _read_int_env("ARGUS_SKILL_BM25_PREFILTER_TOPK", _DEFAULT_TOP_K)
    if top_k <= 0 or top_k >= len(summaries):
        return list(summaries)

    try:
        return _bm25_rank(task_description, summaries, top_k)
    except Exception:  # noqa: BLE001 — never let prefilter break selection
        log.exception("bm25_prefilter failed; falling back to unfiltered list")
        return list(summaries)


def _bm25_rank(
    task_description: str, summaries: Sequence[dict], top_k: int
) -> list[dict]:
    q_tokens = bm25_tokens(task_description)
    if not q_tokens:
        return list(summaries)

    docs: list[list[str]] = [bm25_tokens(_candidate_text(s)) for s in summaries]
    n = len(docs)
    if n == 0:
        return list(summaries)

    # Document frequencies
    df: Counter[str] = Counter()
    for tokens in docs:
        df.update(set(tokens))
    matched_query_tokens = [token for token in q_tokens if token in df]
    if not matched_query_tokens:
        return list(summaries)

    # Inverse document frequency, Okapi formulation (with +0.5 smoothing)
    idf = {
        term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
        for term, freq in df.items()
    }

    avgdl = sum(len(d) for d in docs) / n if n else 0.0
    avgdl = max(avgdl, 1.0)  # avoid 0-division when all docs empty

    scored: list[tuple[float, int]] = []
    for i, tokens in enumerate(docs):
        if not tokens:
            scored.append((0.0, i))
            continue
        tf = Counter(tokens)
        score = 0.0
        for q in matched_query_tokens:
            if q not in tf:
                continue
            idf_q = idf.get(q, 0.0)
            num = tf[q] * (_K1 + 1)
            denom = tf[q] + _K1 * (1 - _B + _B * len(tokens) / avgdl)
            score += idf_q * (num / denom)
        scored.append((score, i))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    keep_idx = [i for _, i in scored[:top_k]]
    return [summaries[i] for i in keep_idx]


__all__ = ["bm25_tokens", "is_prefilter_enabled", "bm25_prefilter"]
