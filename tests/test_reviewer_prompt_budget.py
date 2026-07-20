"""Reviewer prompt size budget — regression guard against prose re-bloat.

The reviewer prompt built by ``_build_prompt`` is assembled and sent every
review round. Its fixed instruction prose had grown to restate the same ideas
3-6x with worked examples; an operator-requested compression cut the decision /
planner-report / checkpoint / step-back prose roughly in half while preserving
every consumed JSON field and every anti-cheat guardrail.

This test pins a CHARACTER BUDGET on the built non-measured prompt so fixed
policy prose cannot silently regrow. Task-specific checklists remain allowed;
role/routing/schema explanations must stay compact.
"""
from __future__ import annotations

import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from argus_skill.reviewer import Reviewer
from argus_skill.reviewer._core import (
    SCHEMA_PATH,
    _compact_schema_for_backend,
)

# The token-efficiency pass reduced this representative prompt from ~40k to
# ~10k chars. Keep modest headroom without permitting role-policy re-bloat.
NON_MEASURED_BUDGET = 14_000


def _build(measured: bool, monkeypatch) -> str:
    if measured:
        monkeypatch.setenv("ARGUS_SKILL_MEASURED_MODE", "1")
    else:
        monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
    r = Reviewer(runner=None, skill_store=None)
    return r._build_prompt(
        objective="minimize cand_ms on the kernel",
        operator_messages=["make the kernel faster"],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="HANDOFF: tried X. RESULT correct=true cand_ms=0.5",
        main_error=None,
        prior_checkpoint={},
    )


def test_non_measured_prompt_within_budget(monkeypatch):
    p = _build(measured=False, monkeypatch=monkeypatch)
    assert len(p) < NON_MEASURED_BUDGET, (
        f"reviewer non-measured prompt is {len(p)} chars, over the "
        f"{NON_MEASURED_BUDGET} budget. The fixed instruction prose has "
        "regrown — re-compress (delete repetition/examples) rather than raising "
        "this cap, unless a genuinely new block was deliberately added."
    )


def test_compression_removed_redundant_examples(monkeypatch):
    # Tie the guard to the actual compression, not just a byte count: these
    # verbose snippets were deleted and must not reappear (they are the
    # redundancy the cut targeted).
    p = _build(measured=False, monkeypatch=monkeypatch)
    assert "you are not a JSON robot" not in p
    assert "Anti-pattern: agent shows test_accuracy=0.98" not in p
    assert "expense_tracker/ package using unittest" not in p


def test_reviewer_records_prompt_block_token_estimates(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_MEASURED_MODE", raising=False)
    reviewer = Reviewer(runner=None, skill_store=None)
    prompt = reviewer._build_prompt(
        objective="audit the current research result",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="RESULT: evidence exists",
        main_error=None,
        prior_checkpoint={},
    )

    stats = reviewer.last_prompt_block_stats
    assert stats["static_total"]["chars"] > 0
    assert stats["delta_total"]["chars"] > 0
    assert stats["main_summary"]["chars"] == len("RESULT: evidence exists")
    assert stats["static_total"]["estimated_tokens"] > 0
    assert stats["static_total"]["chars"] + stats["delta_total"]["chars"] == len(prompt)


def test_backend_schema_is_minified_without_semantic_change() -> None:
    source = Path(SCHEMA_PATH).read_bytes()
    compact_path, compact = _compact_schema_for_backend(SCHEMA_PATH, source)

    assert json.loads(compact) == json.loads(source)
    assert Path(compact_path).read_bytes() == compact
    assert len(compact) < len(source) * 0.6
    assert (len(compact) + 3) // 4 < 1_200


def test_compact_schema_cache_is_safe_under_concurrent_reviewers() -> None:
    source = Path(SCHEMA_PATH).read_bytes()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _index: _compact_schema_for_backend(SCHEMA_PATH, source),
                range(32),
            )
        )

    paths = {path for path, _contract in results}
    assert len(paths) == 1
    cached_path = Path(next(iter(paths)))
    assert cached_path.read_bytes() == results[0][1]
    assert json.loads(cached_path.read_bytes()) == json.loads(source)
