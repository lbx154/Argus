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
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
)

#: Mechanical metric gate (not a paper); the supervisor stops when the metric
#: stops improving rather than on paper-completeness.
completion_gate = "metric"


def role_banner(role: str) -> str:
    """Pin the nanochat-BPB objective at the top of every agent prompt."""
    return (
        "MISSION — NanoChat Autoresearch (Recursive Task 1). This is NOT a\n"
        "speedrun and NOT a paper. The single objective: LOWER the mean\n"
        "validation bits-per-byte (val_bpb) of a small GPT trained FROM SCRATCH\n"
        "in a FIXED 300-second single-GPU budget on B200. Beat 0.9344\n"
        "(optimized_from_vanilla), then 0.9109 (Recursive's best). Start from\n"
        "vanilla 1.0587. Edit ONLY train.py; the metric, 300s budget, val shard,\n"
        "and harness (lib.py) are frozen. Do NOT optimize for wall-time or\n"
        "throughput for its own sake — only the final val_bpb matters.\n"
    )


__all__ = ["REVIEWER_CHECKLISTS", "STAGE_CHECKS", "STAGE_ORDER", "completion_gate", "role_banner"]
