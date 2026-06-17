"""KernelBench / SOL-ExecBench vertical — Recursive "First Steps" **Task 3**.

Objective: MAXIMIZE the hardware **Speed-of-Light (SOL)** score across the GPU
kernels in NVIDIA's SOL-ExecBench (235 kernels), on **B200**. For each kernel
the agent writes a correct implementation whose runtime approaches the kernel's
hardware SOL; the score is the SOL fraction achieved (HIGHER is better,
correctness-gated). This is a KERNEL-SPEED task — NOT bits-per-byte
(``nanochat``) and NOT time-to-target-loss (``nanogpt_speedrun``).

Same 4-stage setup→optimize→measure→report structure; the metric is the SOL
score (maximize), correctness is a hard gate, and there is no paper.
"""
from __future__ import annotations

from ..speedrun.stages import _PIPELINE_CHECK

STAGE_ORDER = ["setup", "optimize", "measure", "report"]

# Metric-agnostic structural checks (the metric is a SOL score, not BPB/time).
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "setup": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md || test -f TASK.md"),
        ("Kernel target(s) / baseline present",
         "{ test -d baseline && ls baseline/* 2>/dev/null | head -1 | grep -q .; } "
         "|| test -d kernels || ls *.cu *.py 2>/dev/null | head -1 | grep -q ."),
        ("Setup notes present",
         "test -f mission/SETUP.md || test -f SETUP.md "
         "|| ls *SETUP*.md 2>/dev/null | head -1 | grep -q ."),
        ("GROUND_TRUTH.md exists with content",
         "test -s research/GROUND_TRUTH.md"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        ("At least one kernel attempt scaffolded",
         "ls attempts/*/* experiments/*/* 2>/dev/null | head -1 | grep -q ."),
    ],
    "measure": [
        _PIPELINE_CHECK,
        ("At least one scored kernel (correct + SOL recorded)",
         "find attempts experiments -name '*.csv' -size +0c 2>/dev/null | head -1 | grep -q . "
         "|| grep -rlsq -iE 'sol|speed.?of.?light|correct' "
         "experiments attempts research 2>/dev/null"),
    ],
    "report": [
        _PIPELINE_CHECK,
        ("RESULTS present",
         "test -f RESULTS.md || test -s research/GROUND_TRUTH.md"),
    ],
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "setup": (
        "engineer/speedrun-setup.md",
        "Evaluate the setup (GATE) for a SOL-score kernel-optimization mission:\n"
        "1. The kernel set / harness identified; how each kernel is BUILT, run for\n"
        "   CORRECTNESS, and TIMED is pinned (the SOL scorer is the source of truth).\n"
        "2. The hardware (B200) and the SOL definition per kernel are pinned.\n"
        "3. research/GROUND_TRUTH.md names the MEASURED bottleneck for the targeted\n"
        "   kernels (memory-bound vs compute-bound, occupancy, etc.) with numbers.\n"
        "Pass: harness + correctness check + SOL scorer + B200 are pinned and the\n"
        "      agent can start producing kernel implementations.",
        ["MISSION.md", "mission/SETUP.md", "research/GROUND_TRUTH.md"],
    ),
    "optimize": (
        "engineer/speedrun-setup.md",
        "Evaluate the latest kernel attempt — FAST loop, keep it LEAN:\n"
        "1. A kernel implementation under attempts/<name>/ that COMPILES.\n"
        "2. A stated, testable hypothesis for why it is faster (tiling, vectorize,\n"
        "   coalescing, occupancy, tensor-core use, …) — not random mutation.\n"
        "3. CHANGES.md present and SHORT (the change + one-line hypothesis).\n"
        "EFFICIENCY: TRUST a clean scorer run + its (correct, SOL%) result; do NOT\n"
        "re-run/re-verify/re-document a recorded score — advance to the next kernel\n"
        "or idea. The metric is the SOL fraction (HIGHER = better), CORRECTNESS-\n"
        "GATED — a faster-but-wrong kernel scores ZERO. The only hard rigor:\n"
        "correctness verified by the harness, real B200 timing, no fabricated SOL.\n"
        "Pass: the kernel is correct and its SOL score is from a clean real run.",
        ["attempts/", "MISSION.md"],
    ),
    "measure": (
        "engineer/speedrun-measure.md",
        "Evaluate the measurement: each kernel attempt verified CORRECT by the\n"
        "harness and timed on B200, the SOL fraction recorded as (kernel, attempt,\n"
        "correct, sol_pct) rows. No fabricated numbers; wrong kernels score 0.\n"
        "Pass: scored rows suffice to compare SOL against the reference.",
        ["attempts/", "MISSION.md"],
    ),
    "report": (
        "engineer/speedrun-report.md",
        "Evaluate the report: RESULTS.md ranking kernels by SOL%, honestly stating\n"
        "which reference SOL scores were beaten and which kernels remain below SOL.\n"
        "No spin. Pass: the headline SOL numbers are verifiable from the table.",
        ["RESULTS.md", "attempts/"],
    ),
}

completion_gate = "metric"


def role_banner(role: str) -> str:
    return (
        "MISSION — KernelBench / SOL-ExecBench (Recursive Task 3). This is a GPU\n"
        "KERNEL-SPEED task, NOT bits-per-byte and NOT time-to-loss. Objective:\n"
        "MAXIMIZE the Speed-of-Light (SOL) score of the kernels on B200 — write\n"
        "CORRECT kernels whose runtime approaches the hardware SOL. Correctness is\n"
        "a hard gate (a fast wrong kernel scores 0). Higher SOL% is better.\n"
    )


__all__ = [
    "STAGE_ORDER", "STAGE_CHECKS", "REVIEWER_CHECKLISTS",
    "completion_gate", "role_banner",
]
