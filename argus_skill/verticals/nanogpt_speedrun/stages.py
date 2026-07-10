"""NanoGPT Speedrun vertical — Recursive "First Steps" **Task 2**.

Objective: MINIMIZE the wall-clock TIME to train a NanoGPT (modded-nanogpt
lineage) down to a FIXED FineWeb validation loss of **3.28** on an **8×H100**
node. The score is SECONDS-TO-TARGET — lower is better. This is a TIME race,
NOT a BPB-minimization (that is the ``nanochat`` vertical) and NOT a kernel SOL
score (``kernelbench``).

Reference times to beat (Recursive, measured on Modal 8×H100):

    from an unoptimized start   ~186.5 s
    Recursive's best run         ~77.3 s   (faster than record #83 on the
                                            same hardware; PrimeIntellect
                                            leaderboard timing pending)

Same 4-stage setup→optimize→measure→report structure; the metric and the
reviewer's objective framing are TIME-to-target, not val_bpb.
"""
from __future__ import annotations

from ..speedrun.stages import (  # shared speedrun checklist + pipeline check
    _PIPELINE_CHECK,
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
)

STAGE_ORDER = ["setup", "optimize", "measure", "report"]

# Metric-agnostic, flat-workspace-tolerant structural checks (no BPB hardcoding:
# the metric here is seconds-to-target-loss, not bits-per-byte).
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "setup": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md || test -f TASK.md"),
        ("Target training script present",
         "{ test -d baseline && ls baseline/*.py 2>/dev/null | head -1 | grep -q .; } "
         "|| ls train*.py *nanogpt*.py 2>/dev/null | head -1 | grep -q ."),
        ("Setup notes present",
         "test -f mission/SETUP.md || test -f SETUP.md "
         "|| ls *SETUP*.md 2>/dev/null | head -1 | grep -q ."),
        ("GROUND_TRUTH.md exists with content",
         "test -s research/GROUND_TRUTH.md"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        ("At least one attempt scaffolded",
         "ls attempts/*/*.py experiments/*/*.py 2>/dev/null | head -1 | grep -q ."),
    ],
    "measure": [
        _PIPELINE_CHECK,
        ("At least one timed-to-target run recorded",
         "find attempts experiments -name '*.csv' -size +0c 2>/dev/null | head -1 | grep -q . "
         "|| grep -rlsq -iE 'val_loss|seconds_to_target|wall_seconds|3\\.28' "
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
        "Evaluate the setup (this stage is a GATE) for a TIME-to-target speedrun:\n"
        "1. Target training script identified and present (modded-nanogpt lineage).\n"
        "2. Harness / data (FineWeb) + the FIXED target val loss 3.28 pinned.\n"
        "3. Hardware budget pinned: 8×H100 node, the timing protocol named.\n"
        "4. research/GROUND_TRUTH.md names the MEASURED binding constraint on\n"
        "   SPEED (e.g. comm/overlap, kernel/throughput, step efficiency, data\n"
        "   loading) WITH numbers from a real baseline run — re-verify it.\n"
        "Pass: target loss + 8×H100 budget + a measured speed bottleneck are\n"
        "      recorded and the agent can start producing faster attempts.",
        ["MISSION.md", "mission/SETUP.md", "research/GROUND_TRUTH.md"],
    ),
    "optimize": (
        "engineer/speedrun-setup.md",
        "Evaluate the latest attempt — FAST loop, keep it LEAN:\n"
        "1. Self-contained training script under attempts/<name>/ that reaches the\n"
        "   target val loss 3.28 on 8×H100.\n"
        "2. The change has a stated, testable hypothesis for WHY it is FASTER to\n"
        "   the target (not random mutation, not a correctness regression).\n"
        "3. CHANGES.md is present and SHORT (the diff + a one-line hypothesis).\n"
        "EFFICIENCY: TRUST a clean timed run + its seconds-to-target; do NOT\n"
        "re-run/re-verify/re-document a recorded time — advance to the next idea.\n"
        "The metric is WALL-CLOCK SECONDS to reach val_loss<=3.28 (lower = better),\n"
        "NOT bits-per-byte. The only hard rigor: the run genuinely reaches 3.28 on\n"
        "8×H100 under the real protocol, and the time is never fabricated.\n"
        "Pass: the attempt reaches the target and its time is from a clean real run.",
        ["attempts/", "MISSION.md"],
    ),
    "measure": (
        "engineer/speedrun-measure.md",
        "Evaluate the measurement: N seeded runs each reaching val_loss<=3.28 on\n"
        "8×H100, with real (not fabricated) wall-clock seconds per run, recorded as\n"
        "(label, seed, seconds_to_target) rows. Pass: rows suffice to compare mean\n"
        "time-to-target against the reference records.",
        ["attempts/", "MISSION.md"],
    ),
    "report": (
        "engineer/speedrun-report.md",
        "Evaluate the report: RESULTS.md with one row per attempt sorted by mean\n"
        "seconds-to-target, honestly stating which reference times (Recursive 77.3s,\n"
        "record #83) were beaten. No spin. Pass: the headline time is verifiable\n"
        "from the table + the per-run records.",
        ["RESULTS.md", "attempts/"],
    ),
}

completion_gate = "metric"


def role_banner(_role: str) -> str:
    return (
        "MISSION — NanoGPT Speedrun (Recursive Task 2). This is a WALL-CLOCK TIME\n"
        "race, NOT a bits-per-byte task and NOT a kernel-SOL task. Objective:\n"
        "MINIMIZE the seconds to train NanoGPT down to FineWeb val_loss <= 3.28 on\n"
        "an 8×H100 node. Beat Recursive's ~77.3 s (and record #83). The score is\n"
        "seconds-to-target — keep correctness (must actually reach 3.28) but make\n"
        "it FASTER; do NOT trade away reaching the target for raw speed.\n"
    )


__all__ = [
    "STAGE_ORDER", "STAGE_CHECKS", "REVIEWER_CHECKLISTS",
    "CHECKLIST_STAGE_ORDER", "CHECKLIST_ITEMS",
    "completion_gate", "role_banner",
]
