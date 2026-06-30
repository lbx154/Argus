"""Reviewer prompt size budget — regression guard against prose re-bloat.

The reviewer prompt built by ``_build_prompt`` is assembled and sent every
review round. Its fixed instruction prose had grown to restate the same ideas
3-6x with worked examples; an operator-requested compression cut the decision /
planner-report / checkpoint / step-back prose roughly in half while preserving
every consumed JSON field and every anti-cheat guardrail.

This test pins a CHARACTER BUDGET on the built non-measured prompt for a fixed
small input so the prose cannot silently regrow back to its pre-compression
size. It is intentionally generous (well above the post-compression size, well
below the pre-compression size): a new genuinely-needed block can fit, but a
wholesale re-expansion of the legislative prose trips it.
"""
from __future__ import annotations

from argus_skill.reviewer import Reviewer

# Post-compression a fixed non-measured prompt measures ~42.5k chars; the
# pre-compression baseline was ~51.5k. Cap at 46k: leaves ~3.5k headroom for a
# legitimately-added block, but catches a regression back toward the old size.
NON_MEASURED_BUDGET = 46_000


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
        checks=[],
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
