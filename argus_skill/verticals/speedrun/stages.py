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

4. **report**: aggregate attempt × repeat rows into a single
   ``RESULTS.md`` table comparing the mission metric / wall time vs the
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

from ...skills.stage_checklists import ChecklistItem

STAGE_ORDER = ["setup", "optimize", "measure", "report"]

# Generic across verticals; kept here as a private copy for now and will
# migrate to ``argus_skill.core.contracts`` once a third vertical lands.
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    # Each check accepts EITHER the canonical speedrun scaffold (MISSION.md,
    # baseline/, reference/, mission/, attempts/) OR a flat task workspace
    # (root train.py, TASK.md, experiments/). Flat workspaces persist reference
    # metrics in research/REFERENCE_SCORES.json or research/GROUND_TRUTH.json;
    # prose-only score mentions are deliberately not accepted as evidence.
    "setup": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md || test -f TASK.md"),
        ("Baseline scripts present",
         "{python} -m argus_skill.verticals.path_evidence --project-root . "
         "--glob 'baseline/*.py' --glob 'train.py'"),
        ("Reference scores present",
         "{python} -m argus_skill.verticals.metric_evidence "
         "speedrun-reference --project-root ."),
        ("Setup notes present",
         "{python} -m argus_skill.verticals.path_evidence --project-root . "
         "--glob 'mission/SETUP.md' --glob 'SETUP.md' --glob '*SETUP*.md'"),
        ("GROUND_TRUTH.md exists with content",
         "test -s research/GROUND_TRUTH.md"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        ("At least one attempt scaffolded",
         "{python} -m argus_skill.verticals.path_evidence --project-root . "
         "--glob 'attempts/*/train.py' --glob 'experiments/*/train*.py'"),
    ],
    "measure": [
        _PIPELINE_CHECK,
        ("At least one attempt has scored seed rows",
         "{python} -m argus_skill.verticals.metric_evidence speedrun --project-root ."),
    ],
    "report": [
        _PIPELINE_CHECK,
        ("Project-root RESULTS.md present",
         "test -s RESULTS.md"),
        ("Structured scored rows remain available",
         "{python} -m argus_skill.verticals.metric_evidence speedrun --project-root ."),
    ],
}

REVIEWER_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    "setup": (
        "engineer/speedrun-setup.md",
        "Evaluate the setup AND the ground-truth diagnosis (this stage is a GATE):\n"
        "1. Target script identified and present under baseline/.\n"
        "2. Harness identified (single import contract, the agent does NOT\n"
        "   rewrite the harness).\n"
        "3. Reference baseline scores present and parsed into a known schema.\n"
        "4. Hardware + wall budget pinned explicitly in mission/SETUP.md.\n"
        "5. NO paper artifacts demanded; this is a code-optimization mission.\n"
        "6. research/GROUND_TRUTH.md exists and contains a BINDING-CONSTRAINT\n"
        "   DIAGNOSIS backed by MEASURED facts. The engineer must have run a\n"
        "   real baseline / profiling pass and READ its ACTUAL telemetry\n"
        "   (utilization, steps completed, tokens seen, the loss/metric\n"
        "   trajectory — whatever the run actually emits, wherever it lives)\n"
        "   and NAMED what actually limits the metric under the fixed budget\n"
        "   (e.g. compute/throughput, model-capacity, undertraining/steps,\n"
        "   or data), WITH the measured numbers that prove it. A guessed or\n"
        "   assumed bottleneck, or a diagnosis with no measured numbers behind\n"
        "   it, FAILS this check.\n"
        "RE-VERIFY the diagnosis yourself: open the same telemetry and confirm\n"
        "the binding constraint the engineer named is what the numbers show —\n"
        "do NOT trust the engineer's summary. Do NOT let the mission advance\n"
        "from 'setup' to 'optimize' while the binding-constraint diagnosis is\n"
        "missing, assumed rather than measured, or unverifiable.\n"
        "Pass: research/GROUND_TRUTH.md names the MEASURED binding constraint\n"
        "      (re-verified) and the agent can start producing attempts/\n"
        "      scripts without further setup work.",
        ["MISSION.md", "mission/SETUP.md", "baseline/", "reference/",
         "research/GROUND_TRUTH.md"],
    ),
    "optimize": (
        "engineer/argus-engineer-role.md",
        "Evaluate the latest attempt — this is a FAST optimization loop; keep it LEAN:\n"
        "1. The change lives in the EDITABLE artifact the mission names (the recipe /\n"
        "   solution file / kernel), self-contained and runnable.\n"
        "2. It does NOT modify the frozen harness/scorer, the metric, the held-out eval,\n"
        "   or the budget — only the editable artifact.\n"
        "3. It fits the declared wall-clock budget, and the change has a stated, testable\n"
        "   hypothesis (why it should move the metric the right way) — not random mutation.\n"
        "4. A SHORT note (CHANGES.md) records the diff + the one-line hypothesis.\n"
        "EFFICIENCY — do NOT slow the loop with bookkeeping:\n"
        "- TRUST a clean run of the mission's frozen scorer and the metric it reports. Do\n"
        "  NOT demand re-running, re-verifying, re-collecting evidence, or extra docs for a\n"
        "  score that is already recorded. Once a candidate's score is in, it is DONE —\n"
        "  advance to the NEXT idea, don't loop re-confirming the last one.\n"
        "- A cheap single-trial screen is fine and preferred; only spend the full\n"
        "  measurement to CONFIRM a candidate that clearly beats the current best.\n"
        "- The ONLY non-negotiable rigor: the real, unmodified evaluation environment (no\n"
        "  fallback/fake/shimmed-away contract), the frozen metric / budget / held-out\n"
        "  eval, and never a fabricated or hardcoded-answer score. Verify THOSE; minimize\n"
        "  everything else.\n"
        "Pass: the attempt is runnable, its hypothesis testable, and (if already scored) the\n"
        "score came from a clean run of the frozen scorer — then ADVANCE.",
        ["attempts/", "MISSION.md"],
    ),
    "measure": (
        "engineer/speedrun-measure.md",
        "Evaluate the measurement:\n"
        "1. N >= the repeat/seed count declared in MISSION.md (when the metric is noisy).\n"
        "2. Each repeat produced a real (not NaN/inf) value of the mission metric.\n"
        "3. Wall clock per run within the declared budget.\n"
        "4. Results recorded as (label, repeat, metric, wall_seconds) rows\n"
        "   matching the reference schema so they can be\n"
        "   concatenated for plotting.\n"
        "5. Honest mean + min + max + (if repeated) 95% CI; no cherry-picked run.\n"
        "Pass: scored rows are sufficient to compare against the reference baseline.",
        ["attempts/", "reference/", "MISSION.md"],
    ),
    "report": (
        "engineer/speedrun-report.md",
        "Evaluate the report:\n"
        "1. RESULTS.md exists at project root.\n"
        "2. Contains a single results table with one row per (attempt,\n"
        "   reference) sorted by the mission metric.\n"
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
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "role_banner",
    "completion_gate",
]


# ===========================================================================
# System (B) — markdown stage checklists for the speedrun vertical
# ===========================================================================
#
# These feed ``argus_skill.skills.stage_checklists`` (the markdown checklist
# that drives the planner/engineer/reviewer round loop) via the optional-hook
# contract in ``argus_skill.verticals._base``. The research vertical re-exports
# the paper floor; the speedrun vertical declares its OWN 4-stage, metric-
# agnostic checklist instead — there is no paper, one number to move the right
# way under a fixed wall-clock budget, whatever that number is (val bpb, kernel
# speedup/SOL, latency, accuracy, …).
#
# The items below are GENERIC across optimization missions: the deliverable/
# eval contract is pinned at ``setup``, the candidate is produced and screened
# at ``optimize``, the repeat-mean / budget measurement happens at ``measure``,
# and the head-to-head baseline comparison is the ``report``. Mission-specific
# nouns (the editable file, the scorer, the metric name, the named baseline)
# come from the operator objective / MISSION.md, not hard-coded here.

#: System-(B) stage order for the speedrun vertical (mirrors STAGE_ORDER).
CHECKLIST_STAGE_ORDER: tuple[str, ...] = ("setup", "optimize", "measure", "report")

#: System-(B) per-stage markdown checklist items for the speedrun vertical.
CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "setup": (
        ChecklistItem(
            id="setup.solution_self_contained",
            statement=(
                "The deliverable is the EDITABLE artifact the mission names (a recipe / "
                "solution file / kernel) that uses the mission's FROZEN harness/scorer "
                "UNCHANGED and modifies ONLY that editable artifact. The reviewer must "
                "confirm the agent did NOT touch the harness, the evaluation, the metric, "
                "the held-out data, or the budget — they are byte-identical to the scaffold "
                "(hash / `git diff` against the pinned scaffold) — and that the candidate is "
                "scored through the mission's frozen scorer exactly as the mission specifies."
            ),
            evidence_hint=(
                "the editable artifact + an unchanged-harness hash vs the pinned scaffold + "
                "a run log produced through the mission's frozen scorer"
            ),
        ),
        ChecklistItem(
            id="setup.heldout_val",
            statement=(
                "Evaluation reads the HELD-OUT / contract-specified inputs wired by the "
                "frozen harness, and there is NO leakage or exploit: the candidate does not "
                "see, memorise, or HARDCODE the known eval inputs/answers, and the metric / "
                "eval inputs are the scaffold's, untouched. The reward must reflect genuine "
                "generalisation, so a candidate that obtains its score by encoding the known "
                "eval distribution (e.g. baking in fixed statistics of the test inputs) "
                "rather than COMPUTING the real result is disqualified — that is a reward "
                "hack, not a result."
            ),
            evidence_hint=(
                "the harness's eval wiring + the candidate showing it COMPUTES the real "
                "result rather than encoding/hardcoding the known eval inputs"
            ),
        ),
    ),
    "optimize": (
        ChecklistItem(
            id="optimize.metric_curve",
            statement=(
                "The mission metric over the run MOVES THE RIGHT WAY (the candidate is "
                "actually improving within the budget), OR any flat / wrong-way / noisy / "
                "early-plateau trajectory is EXPLICITLY explained (e.g. budget-bound, "
                "schedule, warmup, divergence) rather than silently accepted. A trajectory "
                "that never improves over the starting point is a dead attempt, not a result. "
                "No real trajectory means this item is UNSATISFIED: an external launch "
                "rejection may justify a blocked mission, but never optimize-stage completion."
            ),
            evidence_hint=(
                "the metric-vs-step (or vs-wall-clock) series in the run log; a one-line "
                "explanation for any non-improving trajectory"
            ),
        ),
    ),
    "measure": (
        ChecklistItem(
            id="measure.repeat_mean_metric",
            statement=(
                "The reported result is the AGGREGATE mission metric across N repeats "
                "(iterate at small N, report the final number at higher N) — NOT a single "
                "lucky run and NEVER the number the candidate printed about itself. A "
                "per-repeat record captures each run's metric as RE-MEASURED by the VERIFIER "
                "re-running the candidate through the frozen scorer under the identical "
                "protocol; the headline the reviewer trusts is the verifier's, because the "
                "agent edits only the artifact and can self-report anything."
            ),
            evidence_hint=(
                "per-repeat record (run, metric) from the verifier's re-runs + the computed "
                "aggregate; each row traceable to a real frozen-scorer output line"
            ),
        ),
        ChecklistItem(
            id="measure.budget_respected",
            statement=(
                "Every scored run respected the FIXED budget the mission declares (wall-clock "
                "and hardware) — the candidate did not extend, bypass, or hand-tune the "
                "budget, and no scored run exceeded it. The contest is the BEST metric "
                "reachable UNDER the fixed budget, so a candidate that only attains its score "
                "by exceeding the budget is invalid; the budget in the frozen harness stays "
                "unchanged."
            ),
            evidence_hint=(
                "per-run wall-clock within the declared budget in the run log / manifest; "
                "the budget in the frozen harness unchanged"
            ),
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.beats_baseline",
            statement=(
                "The proposed candidate's metric BEATS the RE-MEASURED baseline: the named "
                "reference baseline re-run ON OUR harness and hardware under the identical "
                "protocol (same repeats, same budget, same held-out eval) — NOT a published "
                "number from different hardware. The comparison is head-to-head and cites "
                "BOTH per-run records (ours and the re-measured baseline's) so the win is a "
                "like-for-like delta, not a hardware / protocol artifact. If the candidate "
                "does NOT beat the re-measured baseline, say so plainly and queue a "
                "repair/pivot — do not relabel a loss as a win."
            ),
            evidence_hint=(
                "two per-run records (proposed vs re-measured baseline) under the identical "
                "protocol + the metric delta; baseline re-run on our hardware, not a "
                "published number from other hardware"
            ),
        ),
    ),
}

#: Speedrun missions are done on a metric verdict, not a paper-submission gate.
completion_gate = "metric"


def role_banner(role: str = "engineer") -> str:
    """Top-of-prompt HARD-OVERRIDE banner for the speedrun vertical.

    The default planner/reviewer/engineer prompts bake in the research-paper
    pipeline (research gate, literature grounding, GO/NO-GO, paper draft/
    review/submission, and stage rollback to upstream paper stages). In a
    speedrun (numeric-optimization) mission those assumptions are wrong and
    actively harmful — the planner will refuse to start ("still at the research
    gate"), and the reviewer/planner will roll the state machine back to
    ``research``. This banner is injected at the very TOP of each agent prompt
    so it supersedes all of that framing.

    It is intentionally generic (no hard-coded file names): the concrete
    editable file and scorer come from the operator objective + special
    prompts, so the same banner serves any speedrun task.
    """
    role_norm = (role or "").strip().lower()
    common = (
        "## INVENT — find NEW mechanisms; do not just re-tune existing knobs\n"
        "This mission breaks a record by INVENTION: a new fused kernel, an FP8 / "
        "low-precision GEMM, a new optimizer / attention / precision scheme, "
        "restructured numerics. A parameter reshuffle of the EXISTING recipe (a "
        "step-count tweak, an LR / momentum nudge, moving an existing split) is NOT "
        "an invention and is the FAILURE MODE to avoid. Work in three moves, IN ORDER:\n"
        "  1. PROFILE FIRST — before tuning anything, measure WHERE the wall-clock "
        "goes at the op / kernel level (per-module or per-kernel time, which ops are "
        "unfused, which GEMMs are NOT in FP8, where HBM traffic dominates). Record the "
        "top costs in `research/PROFILE.md`. Invention targets are invisible until you "
        "profile.\n"
        "  2. RESEARCH a technique to attack the top cost — web search, papers, public "
        "kernel/library docs for a GENERAL method (FP8 GEMM patterns, fused Triton "
        "kernels avoiding HBM round-trips, optimizer / Newton-Schulz / noise methods, "
        "attention-kernel variants). Note it in `research/TECHNIQUE_NOTES.md`.\n"
        "  3. IMPLEMENT it yourself as a real NEW mechanism — usually editing "
        "`triton_kernels.py`, not just `train.py` scalars — then score it. A bold, "
        "honestly-measured mechanism that does not YET win is worth FAR more than a "
        "trivial tweak that merely scores.\n"
        "ANTI-CHEAT (HARD): research GENERAL techniques only. You MUST NOT search for, "
        "open, or copy the ANSWER to THIS task — the modded-nanogpt leaderboard or any "
        "record / write-up beyond your given starting point, or any published 'best' / "
        "'optimized' solution to this exact speedrun. General method = ALLOWED; this "
        "task's published solution = DISQUALIFYING.\n"
        "\n"
        "## PIPELINE = OPTIMIZE — hard override of everything below\n"
        "Lean numeric-optimization loop, NOT a research paper. NO paper / draft / "
        "review / submission / EMNLP / GO-NO-GO gate; the only stages are `run` (edit "
        "+ score) and `analysis`; missing paper artifacts are EXPECTED, never a defect "
        "— the stage is never rolled back to research/plan (stage transitions are the "
        "Manager's, not yours), never rebuild a paper literature "
        "gate (short `research/PROFILE.md` and `research/TECHNIQUE_NOTES.md` are fine). "
        "Score only with the frozen scorer the objective names. Run BASIN-HOPPING + "
        "CO-TUNING, not greedy hill-climb: snapshot the lowest-ever VERIFIER-measured "
        "metric as the GLOBAL BEST (the deliverable floor, never lost), but develop an "
        "ACTIVE LINE that may sit ABOVE the floor while a mechanism matures over "
        "several rounds. Done only when the metric target is met or the budget is "
        "spent.\n"
    )
    role_line = {
        "planner": (
            "- As PLANNER: stay in the run stage. Your FIRST mission on a fresh recipe "
            "is PROFILE — produce `research/PROFILE.md` (an op/kernel-level time "
            "breakdown naming the top cost to attack). After that, every line you "
            "queue ATTACKS a profiled top cost with a NEW mechanism (research a "
            "technique, then implement it in the kernel / precision path) — NOT another "
            "tweak of an existing knob. A pure parameter line (step count, LR, split "
            "timing) is the LOWEST-value mission and must NEVER be your opener or your "
            "basin-hop target. Develop the ACTIVE LINE over several co-tuning rounds; "
            "when it stalls (~3 rounds <0.001 or failing), basin-hop to a DIFFERENT op "
            "to attack or a DIFFERENT technique. A maturing mechanism may sit ABOVE the "
            "global best — never kill it after one losing round. Do NOT queue paper / "
            "GO-NO-GO / rollback tasks. Judge project_done purely on the metric.\n"
        ),
        "reviewer": (
            "- As REVIEWER, you are also the INNOVATION COACH, and you run a "
            "BASIN-HOPPING + CO-TUNING search — NOT a greedy single-point "
            "hill-climb. Your job is to keep the verified floor safe while pushing "
            "the search into structurally NEW regions of the design space. "
            "Specifically:\n"
            "  * GLOBAL BEST vs ACTIVE LINE: the lowest-ever VERIFIER-measured mean "
            "metric is the GLOBAL BEST — keep it snapshotted and NEVER lose that "
            "floor. But do NOT demand that every experiment restart from the "
            "global-best SHA; that greedy re-anchoring is exactly what traps the "
            "loop in a local optimum. Track a separate ACTIVE LINE the engineer is "
            "currently developing, which may sit slightly ABOVE the global best "
            "while it matures.\n"
            "  * MATURATION WINDOW: a structural / optimizer / architecture change "
            "usually scores WORSE on round 1 because its supporting hyperparameters "
            "(LR, init, warmup, schedule) do not fit yet, and only wins after 2-4 "
            "rounds of CO-TUNING. Give every new direction a maturation window of "
            "several rounds before judging it; NEVER declare a bold direction dead "
            "after a single losing round — that is the central mistake.\n"
            "  * COMBINE coordinated changes: when a structural change and the "
            "hyperparameters that support it express ONE idea, accept them as ONE "
            "candidate. Do not force one-knob-at-a-time on a method-level move.\n"
            "  * BASIN-HOP when nibbling: if the last ~3 rounds each improved the "
            "global best by <0.001 (or failed), the recipe is in a LOCAL OPTIMUM. "
            "Stop approving further perturbations of it and, in next_action, DEMAND "
            "a NEW active line from a STRUCTURALLY DIFFERENT region — a different "
            "depth/width trade, a different attention scheme, a different optimizer "
            "regime, a different token/step-budget split, a curriculum, a different "
            "normalization/residual scheme. Develop THAT for several rounds EVEN IF "
            "it is temporarily worse than the global best; you are exploring, not "
            "climbing.\n"
            "  * REVERT means revert the ACTIVE LINE's last step — not snap all the "
            "way back to the global-best SHA for the next idea. Always snapshot the "
            "global best so exploration never loses ground, but let the next idea "
            "continue from where the active line is.\n"
            "  * BIAS TO BOLD: at least HALF of your next_action recommendations "
            "must be structural / method-level explorations (new architecture, "
            "optimizer, or training paradigm), not regularizer/init/LR nibbles — the "
            "nibbles are nearly exhausted and the remaining gains live in a "
            "different region of the design space.\n"
            "  * NEVER accept 'no changes / objective complete' while budget remains "
            "and the metric can still drop, and treat a properly measured-and-"
            "reverted bold experiment as GOOD process, not failure. Do NOT flag "
            "missing research/paper artifacts, apply paper/contribution criteria, or "
            "recommend rollback.\n"
        ),
        "engineer": (
            "- As ENGINEER: PROFILE before you tune — if `research/PROFILE.md` is "
            "missing or stale, produce it (op/kernel-level time breakdown) FIRST. Then "
            "each turn attempt the boldest NEW mechanism you can implement correctly to "
            "attack the top profiled cost: research a general technique, implement it "
            "(usually in `triton_kernels.py`, not just `train.py` scalars), and score "
            "it; a multi-round mechanism temporarily BEHIND the floor is EXPECTED and "
            "good. Always snapshot the verified GLOBAL BEST so the floor is never lost; "
            "'revert' rolls back the active line's last step, not a snap-back to the "
            "floor. Do NOT default to a trivial one-knob tweak just to bank a score — "
            "that is a wasted round. Keep short `research/PROFILE.md` / "
            "`research/TECHNIQUE_NOTES.md`; do NOT write paper/draft files.\n"
        ),
    }.get(role_norm, "")
    return common + role_line + "\n"
