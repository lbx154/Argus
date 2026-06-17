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
    # (root train.py, TASK.md, experiments/, reference scores recorded in
    # research/GROUND_TRUTH.md). The OR-branches keep scaffolded projects
    # passing unchanged while a legitimately-set-up flat workspace no longer
    # hard-blocks the setup gate just for missing rigid file names.
    "setup": [
        _PIPELINE_CHECK,
        ("Mission file present",
         "test -f MISSION.md || test -f TASK.md"),
        ("Baseline scripts present",
         "{ test -d baseline && ls baseline/*.py 2>/dev/null | head -1 | grep -q .; } "
         "|| test -f train.py"),
        ("Reference scores present",
         "test -f reference/results/val_bpb.csv || test -f reference/scores.csv "
         "|| test -f reference/results.csv "
         "|| ls reference/*.csv 2>/dev/null | head -1 | grep -q . "
         "|| grep -qs val_bpb research/GROUND_TRUTH.md"),
        ("Setup notes present",
         "test -f mission/SETUP.md || test -f SETUP.md "
         "|| ls *SETUP*.md 2>/dev/null | head -1 | grep -q ."),
        ("GROUND_TRUTH.md exists with content",
         "test -s research/GROUND_TRUTH.md"),
    ],
    "optimize": [
        _PIPELINE_CHECK,
        ("At least one attempt scaffolded",
         "ls attempts/*/train.py experiments/*/train*.py 2>/dev/null | head -1 | grep -q ."),
    ],
    "measure": [
        _PIPELINE_CHECK,
        ("At least one attempt has scored seed rows",
         "find attempts experiments -name 'results.csv' -size +0c 2>/dev/null | head -1 | grep -q . "
         "|| grep -rlsq MEAN_VAL_BPB experiments attempts 2>/dev/null"),
    ],
    "report": [
        _PIPELINE_CHECK,
        ("Project-root RESULTS.md present",
         "test -f RESULTS.md || test -s research/GROUND_TRUTH.md"),
        ("RESULTS.md cites at least one attempt",
         "{ test -f RESULTS.md && grep -qE 'attempts/|experiments/' RESULTS.md; } "
         "|| grep -rlsq MEAN_VAL_BPB experiments attempts 2>/dev/null"),
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
                "that never improves over the starting point is a dead attempt, not a result."
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
        "## INNOVATION IS YOUR PRIMARY DRIVER — read this FIRST, above all else\n"
        "Your #1 objective is to INNOVATE: to invent and land genuinely NEW, BOLD, "
        "STRUCTURAL improvements. Records are broken by INVENTION — a new kernel or "
        "fusion, a new optimizer / attention / precision scheme, a restructured "
        "recipe, curriculum, or numerics — NEVER by timid one-knob nibbles (a "
        "step-count tweak, an LR nudge, a single hyperparameter). Begin EVERY round "
        "by asking: 'what is the BOLDEST, highest-leverage structural change I can "
        "correctly implement here?' — and pursue THAT. Small tuning is permitted "
        "ONLY in service of maturing a bold idea (co-tuning the hyperparameters a "
        "structural change needs), NEVER as the headline move and NEVER as a way to "
        "bank a cheap safe score. A bold, honestly-measured experiment that does not "
        "YET win is MORE valuable than a trivial edit that merely scores — the "
        "trivial safe edit is the FAILURE MODE to avoid, not the goal. Always chase "
        "the largest potential payoff you can implement, even if it takes several "
        "rounds of co-tuning to mature. Innovation first; tuning only in its service.\n"
        "\n"
        "## PIPELINE MODE = OPTIMIZE — HARD OVERRIDE (supersedes EVERYTHING below)\n"
        "This mission is a lean numeric-optimization loop, NOT a research paper. "
        "Ignore every instruction further down that assumes a paper pipeline. "
        "Specifically:\n"
        "- There is NO research gate, NO literature/grounding, NO GO/NO-GO, NO "
        "reference-repo survey, NO paper / draft / review / submission, NO "
        "EMNLP/ACL — for this mission.\n"
        "- The ONLY stages that exist are `run` (edit the recipe + score it) and "
        "`analysis`. Stages research/plan/benchmark/draft/review/submission are "
        "INTENTIONALLY ABSENT; their artifacts being missing is EXPECTED, not a "
        "defect to repair.\n"
        "- NEVER report a 'missing upstream artifact', NEVER call `rollback_stage`, "
        "and NEVER roll the pipeline back to research/plan/benchmark. NEVER build "
        "or rebuild `research/` or grounding artifacts.\n"
        "- Follow the operator objective: edit the single editable recipe file "
        "and score it with the frozen scorer the objective names (lower is "
        "better unless stated). Run a BASIN-HOPPING + CO-TUNING search, NOT a "
        "greedy single-point hill-climb: always snapshot the lowest-ever "
        "VERIFIER-measured metric as the GLOBAL BEST (the deliverable floor, "
        "never lost), but develop a separate ACTIVE LINE that may sit "
        "temporarily ABOVE the global best while a structural change matures "
        "over several rounds of co-tuning. The mission is done when the metric "
        "target is met or the budget is spent — never before.\n"
    )
    role_line = {
        "planner": (
            "- As PLANNER: go straight to the run stage and stay there. INNOVATION "
            "IS WHAT YOU QUEUE FIRST: open the search with a HIGH-LEVERAGE "
            "STRUCTURAL line (a new kernel / fusion, a new optimizer / attention / "
            "precision scheme, or a recipe/curriculum restructuring) — NEVER open "
            "with the smallest or safest tweak (a step-count cut, an LR nudge). "
            "Queue "
            "missions that DEVELOP the current ACTIVE LINE (a recipe edit + its "
            "supporting hyperparameters co-tuned, then re-scored) or that diagnose "
            "why a run did not improve the metric. When the active line has STALLED "
            "(~3 rounds each improving the global best by <0.001, or failing), queue "
            "a BASIN-HOP instead: open a NEW active line from a structurally "
            "different point, even if it scores temporarily WORSE than the verified "
            "GLOBAL BEST (which is always snapshotted and never lost). Do NOT force "
            "every mission to restart from the global-best SHA, and do NOT queue "
            "research/grounding/paper/rollback tasks. Judge project_done purely on "
            "the metric, never on a pipeline checklist.\n"
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
            "- As ENGINEER: the loop is — edit the recipe file, run the frozen "
            "scorer, and DEVELOP the ACTIVE LINE. Always snapshot the verified "
            "GLOBAL BEST (the lowest-ever mean metric) so the floor is never lost, "
            "but carry the active line forward even when a maturing structural "
            "change is temporarily ABOVE the global best — co-tune its LR / init / "
            "warmup / schedule over several rounds before judging it. Combine a "
            "structural change with the hyperparameters that support it into ONE "
            "candidate. 'Revert' rolls back the active line's last step; it does "
            "NOT force a snap-back to the global-best SHA. Each turn, attempt the "
            "BOLDEST structural recipe change you can implement correctly (plus the "
            "co-tuning it needs) and score it — a multi-round structural line that "
            "is temporarily BEHIND the floor is EXPECTED and good. Do NOT default to "
            "the smallest safe edit just to guarantee a scored result; a trivial "
            "one-knob change that merely scores is a wasted round. Do NOT write "
            "research/grounding/paper files.\n"
        ),
    }.get(role_norm, "")
    return common + role_line + "\n"
