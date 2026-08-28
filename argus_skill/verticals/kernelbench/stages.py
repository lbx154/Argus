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

from pathlib import Path

from ...skills.stage_machine import ChecklistItem
from ..optimization_base import speedrun_base_contract

SPEEDRUN_CHECKLIST_ITEMS = speedrun_base_contract().checklist_items

STAGE_ORDER = ["research", "setup", "optimize", "measure", "report"]

completion_gate = "metric"
MISSION_KIND = "optimize"


def stage_completion_issues(stage: str, project_root: Path) -> tuple[str, ...]:
    from ..metric_evidence import EvidenceError, validate_kernelbench_evidence

    issues: list[str] = []
    if stage == "measure":
        try:
            validate_kernelbench_evidence(project_root)
        except EvidenceError as exc:
            issues.append(str(exc))
    if stage == "report":
        report = project_root / "RESULTS.md"
        if not report.is_file() or report.stat().st_size <= 0:
            issues.append("report requires non-empty RESULTS.md")
    return tuple(issues)

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
    "STAGE_ORDER",
    "CHECKLIST_STAGE_ORDER", "CHECKLIST_ITEMS",
    "completion_gate", "role_banner", "stage_completion_issues",
]
