"""KernelBench / SOL-ExecBench vertical — Recursive "First Steps" **Task 3**.

Objective: MAXIMIZE the hardware **Speed-of-Light (SOL)** score across the GPU
kernels in NVIDIA's SOL-ExecBench (235 kernels), on **B200**. For each kernel
the agent writes a correct implementation whose runtime approaches the kernel's
hardware SOL; the score is the SOL fraction achieved (HIGHER is better,
correctness-gated). This is a KERNEL-SPEED task — NOT bits-per-byte
(``nanochat``) and NOT time-to-target-loss (``nanogpt_speedrun``).

Kernel work still needs **research**: the agent must understand the scorer,
hardware roofline, and public optimization patterns before trying to beat the
benchmark. But this is benchmark research, not paper-production research: it
must directly enable first score and SOTA-oriented optimization.
"""
from __future__ import annotations

from ...skills.stage_machine import ChecklistItem
from ..speedrun.stages import _PIPELINE_CHECK
from ..speedrun.stages import CHECKLIST_ITEMS as SPEEDRUN_CHECKLIST_ITEMS

STAGE_ORDER = ["research", "setup", "optimize", "measure", "report"]

# Metric-agnostic structural checks (the metric is a SOL score, not BPB/time).
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "research": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md || test -f TASK.md"),
        ("Ground truth / scorer facts present",
         "test -s research/GROUND_TRUTH.md"),
        ("Technique grounding present",
         "test -s research/TECHNIQUE_NOTES.md "
         "|| test -s research/LITERATURE_GROUNDING.json "
         "|| test -s research/LIT_MATRIX.tsv"),
        ("First-score plan present",
         "test -s research/FIRST_SCORE_PLAN.md "
         "|| test -s research/RESEARCH_BRIEF.md"),
    ],
    "setup": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md || test -f TASK.md"),
        ("Kernel target(s) / baseline present",
         "{python} -m argus_skill.verticals.path_evidence --project-root . "
         "--glob 'baseline/*' --glob 'kernels/**/*' --glob '*.cu' --glob '*.py'"),
        ("Setup notes present",
         "{python} -m argus_skill.verticals.path_evidence --project-root . "
         "--glob 'mission/SETUP.md' --glob 'SETUP.md' --glob '*SETUP*.md'"),
        ("GROUND_TRUTH.md exists with content",
         "test -s research/GROUND_TRUTH.md"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        ("At least one kernel attempt scaffolded",
         "{python} -m argus_skill.verticals.path_evidence --project-root . "
         "--glob 'attempts/*/*' --glob 'experiments/*/*'"),
    ],
    "measure": [
        _PIPELINE_CHECK,
        ("At least one scored kernel (correct + SOL recorded)",
         "{python} -m argus_skill.verticals.metric_evidence kernelbench --project-root ."),
    ],
    "report": [
        _PIPELINE_CHECK,
        ("RESULTS present",
         "test -f RESULTS.md || test -s research/GROUND_TRUTH.md"),
    ],
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "research": (
        "engineer/sol-kernel-sota-optimization.md",
        "Evaluate the kernel-benchmark RESEARCH stage (this is SOTA-enabling, "
        "not paper research):\n"
        "1. The frozen scorer/harness and target kernel are understood from the "
        "project files or mission statement.\n"
        "2. `research/GROUND_TRUTH.md` records measured facts: hardware, scorer "
        "entry point, correctness rule, baseline or current-state observation.\n"
        "3. External technique grounding exists: public SOL/KernelBench/Triton/"
        "CUDA/CUTLASS docs, papers, or issue/write-up patterns are summarized "
        "as candidate optimization ideas. Do not require 10 papers or a paper "
        "literature review; require useful kernel tactics.\n"
        "4. A first-score plan exists: the next stage knows exactly what harness "
        "command to run, which editable file/kernel to change, and what metric "
        "JSON/table proves progress.\n"
        "Pass: research directly enables the first correct scored attempt.",
        [
            "MISSION.md",
            "research/GROUND_TRUTH.md",
            "research/TECHNIQUE_NOTES.md",
            "research/FIRST_SCORE_PLAN.md",
        ],
    ),
    "setup": (
        "engineer/sol-kernel-sota-optimization.md",
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
        "engineer/sol-kernel-sota-optimization.md",
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
        "engineer/sol-kernel-sota-optimization.md",
        "Evaluate the measurement: each kernel attempt verified CORRECT by the\n"
        "harness and timed on B200, the SOL fraction recorded as (kernel, attempt,\n"
        "correct, sol_pct) rows. No fabricated numbers; wrong kernels score 0.\n"
        "Pass: scored rows suffice to compare SOL against the reference.",
        ["attempts/", "MISSION.md"],
    ),
    "report": (
        "engineer/sol-kernel-sota-optimization.md",
        "Evaluate the report: RESULTS.md ranking kernels by SOL%, honestly stating\n"
        "which reference SOL scores were beaten and which kernels remain below SOL.\n"
        "No spin. Pass: the headline SOL numbers are verifiable from the table.",
        ["RESULTS.md", "attempts/"],
    ),
}

completion_gate = "metric"

CHECKLIST_STAGE_ORDER: tuple[str, ...] = (
    "research",
    "setup",
    "optimize",
    "measure",
    "report",
)

CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": (
        ChecklistItem(
            id="research.scorer_ground_truth",
            statement=(
                "The agent has recorded the frozen scorer/harness facts: target "
                "kernel or editable file, correctness rule, hardware, command to "
                "run, and the baseline/current measurement if available."
            ),
            evidence_hint="research/GROUND_TRUTH.md",
        ),
        ChecklistItem(
            id="research.external_kernel_patterns",
            statement=(
                "The agent searched or inspected external/public kernel-optimization "
                "patterns relevant to this task (SOL/KernelBench/Triton/CUDA/CUTLASS/"
                "roofline docs, papers, or issue/write-ups) and distilled concrete "
                "candidate tactics. This is not a paper literature review; it is "
                "SOTA-oriented technique research."
            ),
            evidence_hint="research/TECHNIQUE_NOTES.md or research/LITERATURE_GROUNDING.json",
        ),
        ChecklistItem(
            id="research.first_score_plan",
            statement=(
                "There is a first-score plan naming the project-local command, the "
                "editable implementation file/kernel, the metric to improve, and the "
                "JSON/table artifact that will prove correctness and speed."
            ),
            evidence_hint="research/FIRST_SCORE_PLAN.md or research/RESEARCH_BRIEF.md",
        ),
    ),
    **SPEEDRUN_CHECKLIST_ITEMS,
}


def role_banner(_role: str) -> str:
    return (
        "MISSION — KernelBench / SOL-ExecBench (Recursive Task 3). This is a GPU\n"
        "KERNEL-SPEED task, NOT bits-per-byte and NOT time-to-loss. Objective:\n"
        "MAXIMIZE the Speed-of-Light (SOL) score of the kernels on B200 — write\n"
        "CORRECT kernels whose runtime approaches the hardware SOL. Correctness is\n"
        "a hard gate (a fast wrong kernel scores 0). Higher SOL% is better.\n"
    )


__all__ = [
    "STAGE_ORDER", "STAGE_CHECKS", "REVIEWER_CHECKLISTS",
    "CHECKLIST_STAGE_ORDER", "CHECKLIST_ITEMS",
    "completion_gate", "role_banner",
]
