"""NanoChat Autoresearch vertical — Recursive "First Steps" **Task 1**.

Objective: MINIMIZE the mean validation bits-per-byte (``val_bpb``) of a small
GPT trained from scratch under a FIXED 300-second single-GPU budget (B200),
scored by the frozen harness over N seeds. Reference scores to beat (Recursive,
single B200, 10-seed mean):

    vanilla_transformer       1.0587   (the naive baseline / start point)
    optimized_from_vanilla    0.9344   (first target to beat)
    optimized_from_karpathy   0.9109   (Recursive's best — the bar)

This is its OWN vertical, DISTINCT from the nanoGPT *speedrun* (minimize wall
TIME to a target loss) and KernelBench/SOL (maximize a Speed-of-Light score)
verticals. It reuses the generic 4-stage setup→optimize→measure→report
structure and the flat-workspace STAGE_CHECKS / reviewer checklists (which are
already BPB-shaped); only the role banner pins the nanochat objective.
"""
from __future__ import annotations

# Reuse the BPB-shaped structure + flat-workspace checks from the generic
# optimization vertical. This is code reuse, not identity: this module is its
# OWN named vertical (so the nanochat task is never classified as "speedrun"),
# free to diverge from speedrun's checklists later.
from ..speedrun.stages import (  # noqa: F401  (re-exported as this vertical's contract)
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
)

#: Mechanical metric gate (not a paper); the supervisor stops when the metric
#: stops improving rather than on paper-completeness.
completion_gate = "metric"


#: The productive, mechanism-CHANGING optimization axes for the 300s-budget
#: from-scratch LM task, biggest-lever-first. The planner is steered to spend
#: candidates here instead of re-sweeping a saturated scalar knob.
_CATEGORY_AXES = (
    "1. OPTIMIZER ALGORITHM — the biggest known lever for fixed-budget "
    "from-scratch LM training: Muon (Newton-Schulz orthogonalized momentum), "
    "Lion, Sophia, Shampoo/SOAP, schedule-free AdamW, Adam-mini; and their "
    "momentum/preconditioner/decoupling.\n"
    "2. ARCHITECTURE — QK-norm, RMSNorm placement (pre/post/sandwich), "
    "RoPE/positional scheme, GQA/MQA, sliding-window/local attention, SwiGLU "
    "hidden sizing, embedding tying/untying, logit soft-cap, value/residual "
    "scaling, depth<->width reshape at fixed params.\n"
    "3. EFFECTIVE-UPDATE MECHANICS — EMA / weight-averaging (Polyak/SWA), "
    "z-loss, label smoothing, grad-clip regime, lr x batch scaling laws.\n"
    "4. DATA — sequence packing, ordering/curriculum, dedup, doc boundaries.\n"
    "5. NUMERICS & INIT — init scale, muP-style width scaling, fp8/bf16 matmul, "
    "QK clipping."
)


def role_banner(role: str) -> str:
    """Pin the nanochat-BPB objective; steer the PLANNER off knob-tweak ruts.

    The banner is role-aware: every role gets the frozen-constraint mission
    framing, but the PLANNER additionally gets a hard SEARCH-DISCIPLINE rule
    that forbids re-sweeping a saturated scalar hyperparameter and mandates a
    category-level change per candidate (the engineer/reviewer get the matching
    reinforcement). This is what stops the a00x LR/WD/batch micro-tweak loop.
    """
    common = (
        "MISSION — NanoChat Autoresearch (Recursive Task 1). This is NOT a\n"
        "speedrun and NOT a paper. The single objective: LOWER the mean\n"
        "validation bits-per-byte (val_bpb) of a small GPT trained FROM SCRATCH\n"
        "in a FIXED 300-second single-GPU budget on B200. Beat 0.9344\n"
        "(optimized_from_vanilla), then 0.9109 (Recursive's best). Start from\n"
        "vanilla 1.0587. Edit ONLY train.py; the metric, 300s budget, val shard,\n"
        "and harness (lib.py) are frozen. Do NOT optimize for wall-time or\n"
        "throughput for its own sake — only the final val_bpb matters.\n"
    )
    if role == "planner":
        return common + (
            "\nSEARCH DISCIPLINE (HARD RULE — overrides the safe-incremental pull):\n"
            "Before proposing the next candidate, READ the attempt history "
            "(attempts/, RESULTS.md). A lone single-scalar tweak (peak LR, "
            "weight-decay, batch size, warmup/warmdown/final-LR fraction, dropout) "
            "is worth AT MOST one value. If the recent screens are single-knob "
            "tweaks clustering within ~0.002 BPB of the verified floor, that basin "
            "is SATURATED: do NOT propose another value of an already-swept knob — "
            "that is wasted 300s budget.\n"
            "EVERY new candidate MUST introduce a CATEGORY-LEVEL change (it MAY "
            "also move hyperparameters, but a category change is mandatory). Pick "
            "the biggest UNEXPLORED lever, roughly in this order:\n"
            f"{_CATEGORY_AXES}\n"
            "Prefer high-variance, mechanism-CHANGING bets that can break the basin "
            "over safe knob-twiddling that only polishes it. The gap to 0.9344 is "
            "~0.09 — 0.000x knob noise will never close it; a different optimizer or "
            "architecture might. Name the axis each candidate explores and keep "
            "diversifying so the search does not collapse back onto scalar tuning.\n"
        )
    if role == "engineer":
        return common + (
            "\nWhen the task is a CATEGORY change (new optimizer / architecture / "
            "data scheme), implement it FAITHFULLY and correctly end-to-end — a "
            "correct, informative REGRESSION is more valuable than a safe "
            "within-noise non-result, so do not water a bold bet down into a knob "
            "tweak. Still 1-seed screen first; keep lib.py and the scorer frozen; "
            "real flash_attn.cute FA-4 only (never SDPA/fallback/FA2).\n"
        )
    if role == "reviewer":
        return common + (
            "\nINNOVATION CHECK: if the screened candidate is yet another single-"
            "scalar tweak landing within ~0.002 of the floor, say so plainly and "
            "record in the handoff that the NEXT candidate MUST be a category-level "
            "change (optimizer / architecture / data) — do not let the loop keep "
            "re-sweeping a saturated knob. Still verify the hard gates: real FA-4, "
            "frozen lib.py, honest real-run score.\n"
        )
    return common


__all__ = [
    "REVIEWER_CHECKLISTS",
    "STAGE_CHECKS",
    "STAGE_ORDER",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "completion_gate",
    "role_banner",
]
