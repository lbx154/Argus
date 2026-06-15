"""Speedrun-vertical stage definitions.

A vertical for **quantitative optimization missions** with a wall-time
budget — kernel optimization, training-script speedrun, autoresearch
benchmarks like ``nanochat_autoresearch`` and SOL-ExecBench.

There is no paper. There is one number to minimize (or maximize) under
a hard wall-clock budget, scored by a fixed harness, evaluated over N
seeds. The verdict is mechanical, not narrative.

The 4 stages:

1. **setup**: pin the target (script to attack), the harness
   (``solutions/lib.py``), the reference baseline scores, and the
   hardware budget. Output: ``mission/SETUP.md``.

2. **optimize**: produce attempts under ``attempts/<name>/train.py``,
   each a self-contained training script that fits the harness
   contract. Output: at least one such script per round.

3. **measure**: run each attempt for N seeds, score via the harness,
   record per-seed rows in ``attempts/<name>/results.csv``. Output:
   one row per (attempt × seed).

4. **report**: aggregate attempt × seed rows into a single
   ``RESULTS.md`` table comparing mean BPB / wall time vs the
   reference baselines, with honest CI. No prose beyond a one-paragraph
   "what changed and what didn't" per attempt.

Compared to the research vertical (which has 8 stages, paper artifacts,
literature gates, reviewer checklists per stage), this vertical is
deliberately *small* — most of the supervisor work happens inside the
single ``optimize`` stage where engineer iterates code, and the
mechanical ``measure`` + ``report`` stages take seconds. This is the
right shape for "the agent writes code, the harness scores it" tasks
where there is nothing to write up.
"""
from __future__ import annotations

STAGE_ORDER = ["setup", "optimize", "measure", "report"]

# Generic across verticals; kept here as a private copy for now and will
# migrate to ``argus_skill.core.contracts`` once a third vertical lands.
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "setup": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md"),
        ("Baseline scripts present",
         "test -d baseline && ls baseline/*.py 2>/dev/null | head -1 | grep -q ."),
        ("Reference scores present",
         "test -f reference/results/val_bpb.csv || test -f reference/scores.csv || test -f reference/results.csv"),
        ("Setup notes present",
         "test -f mission/SETUP.md"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        ("At least one attempt scaffolded",
         "ls attempts/*/train.py 2>/dev/null | head -1 | grep -q ."),
    ],
    "measure": [
        _PIPELINE_CHECK,
        ("At least one attempt has scored seed rows",
         "find attempts -name 'results.csv' -size +0c 2>/dev/null | head -1 | grep -q ."),
    ],
    "report": [
        _PIPELINE_CHECK,
        ("Project-root RESULTS.md present",
         "test -f RESULTS.md"),
        ("RESULTS.md cites at least one attempt",
         "test -f RESULTS.md && grep -q 'attempts/' RESULTS.md"),
    ],
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "setup": (
        "engineer/speedrun-setup.md",
        "Evaluate the setup:\n"
        "1. Target script identified and present under baseline/.\n"
        "2. Harness identified (single import contract, the agent does NOT\n"
        "   rewrite the harness).\n"
        "3. Reference baseline scores present and parsed into a known schema.\n"
        "4. Hardware + wall budget pinned explicitly in mission/SETUP.md.\n"
        "5. NO paper artifacts demanded; this is a code-optimization mission.\n"
        "Pass: the agent can start producing attempts/ scripts without\n"
        "      further setup work.",
        ["MISSION.md", "mission/SETUP.md", "baseline/", "reference/"],
    ),
    "optimize": (
        "engineer/speedrun-optimize.md",
        "Evaluate the latest attempt:\n"
        "1. Self-contained single-file script under attempts/<name>/train.py.\n"
        "2. Imports the unmodified harness from baseline/lib.py (or an\n"
        "   identity copy).\n"
        "3. Fits the wall-clock budget declared in MISSION.md.\n"
        "4. CHANGES.md documents what differs from the parent baseline\n"
        "   it branched from.\n"
        "5. The change has a stated hypothesis (why this should lower\n"
        "   BPB) — not random mutation.\n"
        "Pass: the attempt is runnable and the hypothesis is testable.",
        ["attempts/", "baseline/lib.py", "MISSION.md"],
    ),
    "measure": (
        "engineer/speedrun-measure.md",
        "Evaluate the measurement:\n"
        "1. N >= the seed count declared in MISSION.md (default 10).\n"
        "2. Each seed produced a real (not NaN/inf) BPB.\n"
        "3. Wall clock per seed within the declared budget.\n"
        "4. Results recorded as (label, seed, val_bpb, wall_seconds) rows\n"
        "   matching the reference results.csv schema so they can be\n"
        "   concatenated for plotting.\n"
        "5. Honest mean + min + max + 95% CI computed; no cherry-picked seed.\n"
        "Pass: scored rows are sufficient to compare against reference.",
        ["attempts/", "reference/", "MISSION.md"],
    ),
    "report": (
        "engineer/speedrun-report.md",
        "Evaluate the report:\n"
        "1. RESULTS.md exists at project root.\n"
        "2. Contains a single results table with one row per (attempt,\n"
        "   reference) sorted by mean BPB.\n"
        "3. States honestly which reference rows were beaten and which\n"
        "   were not; no spin.\n"
        "4. One-paragraph 'what changed' per attempt, cross-referencing\n"
        "   attempts/<name>/CHANGES.md.\n"
        "5. No prose beyond what's needed to read the table.\n"
        "Pass: a reader can verify the headline number from the table\n"
        "      + the CSVs in attempts/.",
        ["RESULTS.md", "attempts/", "reference/"],
    ),
}

__all__ = [
    "STAGE_ORDER",
    "STAGE_CHECKS",
    "REVIEWER_CHECKLISTS",
    "_PIPELINE_CHECK",
]
