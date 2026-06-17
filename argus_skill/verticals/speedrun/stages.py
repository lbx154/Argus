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
        "engineer/nanochat-pretrain-runner.md",
        "Evaluate the latest attempt — this is a FAST optimization loop; keep it LEAN:\n"
        "1. Self-contained single-file script under attempts/<name>/train.py.\n"
        "2. Imports the unmodified harness from baseline/lib.py (or an identity copy).\n"
        "3. Fits the wall-clock budget, and the change has a stated, testable\n"
        "   hypothesis (why this should lower BPB) — not random mutation.\n"
        "4. CHANGES.md is present and SHORT (the diff + a one-line hypothesis).\n"
        "EFFICIENCY — do NOT slow the loop with bookkeeping:\n"
        "- TRUST a clean `./eval_solution.sh` exit and its `MEAN_VAL_BPB`. Do NOT demand\n"
        "  re-running, re-verifying, re-collecting evidence, or extra documentation for a\n"
        "  score that is already recorded. Once a candidate's score is in, it is DONE —\n"
        "  advance to the NEXT idea, don't loop re-confirming the last one.\n"
        "- Screening a candidate with 1 seed (`./eval_solution.sh train.py 1`) is fine and\n"
        "  preferred; only spend the full seed count to CONFIRM a candidate that clearly\n"
        "  beats the current floor.\n"
        "- The ONLY non-negotiable rigor: real `flash_attn.cute` (no SDPA/fallback/fake\n"
        "  kernels), the frozen metric / 300s budget / val shard, and never a fabricated\n"
        "  score. Verify THOSE; minimize everything else.\n"
        "Pass: the attempt is runnable, its hypothesis testable, and (if already scored) the\n"
        "score came from a clean real-FA-4 scorer run — then ADVANCE.",
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
# the paper floor; the speedrun vertical declares its OWN 4-stage,
# nanochat-shaped checklist instead — there is no paper, one number to lower
# (mean validation bits-per-byte) under a fixed wall-clock budget.
#
# The items below are ported from the nanochat-autoresearch substitution that
# previously lived inline in ``stage_checklists.py``; they are mapped onto the
# speedrun stages: the deliverable/eval contract is pinned at ``setup``, the
# learning recipe is produced at ``optimize``, the seed-mean / budget
# measurement happens at ``measure``, and the head-to-head baseline comparison
# is the ``report``.

#: System-(B) stage order for the speedrun vertical (mirrors STAGE_ORDER).
CHECKLIST_STAGE_ORDER: tuple[str, ...] = ("setup", "optimize", "measure", "report")

#: System-(B) per-stage markdown checklist items for the speedrun vertical.
CHECKLIST_ITEMS: dict[str, tuple[ChecklistItem, ...]] = {
    "setup": (
        ChecklistItem(
            id="setup.solution_self_contained",
            statement=(
                "The deliverable is ONE self-contained training script "
                "`solutions/<name>.py` (a `solution.py`) that imports the shared "
                "harness `lib.py` (tokenizer, dataloader, `evaluate_bpb` on the "
                "held-out shard, `TIME_BUDGET=300`) UNCHANGED and modifies ONLY "
                "the training recipe inside that one script. The reviewer must "
                "confirm the agent did NOT touch `lib.py`, the evaluation, the "
                "validation set, or the budget — `lib.py` is byte-identical to "
                "the scaffold (hash/`git diff` against "
                "`/scratch/recursive/nanochat_autoresearch`) — and that the "
                "script runs via `/scratch/run_with_shim.py` (transparently swaps "
                "flash-attn-3 -> torch SDPA, since an A100 cannot run the "
                "Hopper-only FA3) or already uses torch SDPA directly."
            ),
            evidence_hint=(
                "solutions/<name>.py + unchanged lib.py hash vs the scaffold at "
                "/scratch/recursive/nanochat_autoresearch + a run log produced "
                "through /scratch/run_with_shim.py"
            ),
        ),
        ChecklistItem(
            id="setup.heldout_val",
            statement=(
                "Evaluation reads the HELD-OUT validation shard wired by `lib.py` "
                "(`shard_06542`), and there is NO train/val leakage: the data the "
                "recipe trains on never includes the held-out shard, and the "
                "tokenizer / metric / val set are the scaffold's, untouched. The "
                "reward must reflect generalisation to UNSEEN bytes, so a recipe "
                "that trains on (or otherwise lets the model see) the val shard is "
                "disqualified — a low val bpb obtained by memorising the val text "
                "is not a result."
            ),
            evidence_hint=(
                "lib.py val-shard wiring (shard_06542) + the recipe's data "
                "selection in solutions/<name>.py showing the val shard is "
                "excluded from training"
            ),
        ),
    ),
    "optimize": (
        ChecklistItem(
            id="optimize.bpb_curve",
            statement=(
                "The validation bpb trajectory over the run DESCENDS (the model "
                "is actually learning within the 300s budget), OR any flat / "
                "rising / noisy / early-plateau curve is EXPLICITLY explained "
                "(e.g. budget-bound underfit, LR schedule, warmup, divergence) "
                "rather than silently accepted. A curve that never improves over "
                "the random-init starting bpb is a dead recipe, not a result."
            ),
            evidence_hint=(
                "val_bpb-vs-step (or vs-wall-clock) series in progress.jsonl / "
                "the run log; a one-line explanation for any non-descending curve"
            ),
        ),
    ),
    "measure": (
        ChecklistItem(
            id="measure.seed_mean_bpb",
            statement=(
                "The reported result is the MEAN validation bits-per-byte (val "
                "bpb, lower=better) across N random seeds (`SEED` env per run; "
                "iterate at N=3-5, report the final number at higher N) — NOT a "
                "single lucky seed and NEVER the number the training script "
                "printed about itself. A per-seed CSV records each seed's "
                "`val_bpb:` line as RE-MEASURED by the VERIFIER re-running the "
                "agent's `solution.py` under the identical protocol; the headline "
                "mean the reviewer trusts is the verifier's, because the agent may "
                "change only the recipe and can self-report anything."
            ),
            evidence_hint=(
                "per-seed CSV (seed,val_bpb) from the verifier's re-runs + the "
                "computed mean; each row traceable to a `val_bpb:` stdout line"
            ),
        ),
        ChecklistItem(
            id="measure.budget_respected",
            statement=(
                "Every scored run respected the FIXED 300s wall-clock "
                "`TIME_BUDGET` on ONE A100-40GB — the recipe did not extend, "
                "bypass, or hand-tune the budget, and no scored seed ran past it. "
                "The contest is the LOWEST mean val bpb reachable UNDER the fixed "
                "budget, so a solution that only attains its bpb by exceeding 300s "
                "is invalid; `TIME_BUDGET` in `lib.py` stays unchanged."
            ),
            evidence_hint=(
                "per-run wall-clock (start/end) <= ~300s in the run log / "
                "manifest; TIME_BUDGET=300 in lib.py unchanged"
            ),
        ),
    ),
    "report": (
        ChecklistItem(
            id="report.beats_baseline",
            statement=(
                "The proposed `solution.py`'s mean val bpb BEATS the RE-MEASURED "
                "baseline: Recursive's best released solution (e.g. "
                "`optimized_from_karpathy.py`) re-run ON OUR harness and A100 "
                "hardware under the identical protocol (same N seeds, 300s budget, "
                "held-out shard) — NOT the baseline's published B200 number "
                "(0.9109). The comparison is head-to-head and cites BOTH per-seed "
                "CSVs (ours and the re-measured baseline's) so the win is a "
                "like-for-like mean-bpb delta, not a hardware / protocol artifact. "
                "If the proposed recipe does NOT beat the re-measured baseline, "
                "say so plainly and queue a repair/pivot — do not relabel a loss "
                "as a win."
            ),
            evidence_hint=(
                "two per-seed CSVs (proposed vs re-measured baseline) under the "
                "identical protocol + the mean-bpb delta; baseline = "
                "optimized_from_karpathy.py re-run on A100, not the published 0.9109"
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
            "- As PLANNER: go straight to the run stage and stay there. Queue "
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
            "NOT force a snap-back to the global-best SHA. Land one concrete recipe "
            "edit + its scored result per turn. Do NOT write research/grounding/"
            "paper files.\n"
        ),
    }.get(role_norm, "")
    return common + role_line + "\n"
