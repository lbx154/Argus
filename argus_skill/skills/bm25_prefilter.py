"""Optional BM25 prefilter for the skill matcher.

When the skill pool is small (N <= 200 by default), the LLM matcher
itself is the cheapest precise selector: a single call to a small router
model (gpt-4o-mini / haiku-3.5) judges all candidates in one pass for
~$0.05 — see `loop.py:resolved_matcher_model` env note. No prefilter is
needed; adding one only burns latency.

When the pool grows past ~200, the matcher prompt starts to crowd the
small-model context window (each candidate summary is ~50 tokens, so
500 candidates = 25k tokens just for the listing). At that point a
cheap BM25 prefilter to top-K=20 keeps the matcher honest *and* the
prompt small. This module implements that prefilter as a clean opt-in
hook that the `SkillStore.find_relevant` matcher can call before
shipping to the LLM.

Design notes
------------

* **No new runtime dependency.** Pure-stdlib BM25 (Okapi variant), based
  on the standard formulation; same result shape as `rank_bm25` so we
  can swap to that library later without changing call sites.
* **Lowercased, alphanumeric-token, ≥3 chars.** Same tokenization rule
  the matcher cache already uses (`SkillStore._normalize_tokens`) so the
  cache-key fingerprint and BM25 score derive from the same vocabulary.
* **Failure mode = fall back to LLM-only.** Any tokenization or
  index-building error → return the unfiltered candidate list and let
  the LLM matcher take over. Selection accuracy must never regress just
  because the prefilter tripped.
* **Threshold is env-tunable.** Default 200 is conservative: empirically
  argus pools sit at 50-100, so the prefilter stays inactive until the
  store actually grows. Set ``ARGUS_SKILL_BM25_PREFILTER_THRESHOLD=0`` to
  force it on for debugging.

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
from collections import Counter
from typing import Any, Sequence

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_THRESHOLD = 200
_DEFAULT_TOP_K = 20

# BM25 hyperparameters — Okapi defaults from Robertson & Zaragoza (2009).
_K1 = 1.5
_B = 0.75


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    lowered = text.lower().replace("_", " ").replace("-", " ")
    return [t for t in _TOKEN_RE.findall(lowered) if len(t) >= 3]


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

    Returns at most ``top_k`` summaries, in original order if BM25 fails.
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
    q_tokens = _tokenize(task_description)
    if not q_tokens:
        return list(summaries)[:top_k]

    docs: list[list[str]] = [_tokenize(_candidate_text(s)) for s in summaries]
    n = len(docs)
    if n == 0:
        return []

    # Document frequencies
    df: dict[str, int] = Counter()
    for tokens in docs:
        df.update(set(tokens))

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
        for q in q_tokens:
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


__all__ = ["is_prefilter_enabled", "bm25_prefilter"]
