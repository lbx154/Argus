"""Research-vertical stage definitions and reviewer checklists.

Authoritative location for the 8 paper-pipeline stages
(research → plan → benchmark → run → analysis → draft → review →
submission) and the per-stage shell checks + reviewer checklists.

This module is the **vertical-specific** half of the stage system.
The generic shell-check runner lives at
``argus_skill.tools.stage_check`` and consumes ``STAGE_ORDER``,
``STAGE_CHECKS``, and ``REVIEWER_CHECKLISTS`` from this module via
re-export. Future verticals (quant, rollout, …) will define their
own ``stages.py`` next to this one with their own stage list.

The shell ``_PIPELINE_CHECK`` is the only check that is genuinely
generic across all verticals (every vertical needs a pipeline-state
file) — it is re-exported here for the research stages but should
migrate to ``argus_skill.core.contracts`` if a second vertical lands
that needs it.
"""
from __future__ import annotations

from pathlib import Path

from ...skills.stage_machine import ChecklistItem
from ...skills.venue_profiles import VenueProfile, resolve_venue_profile

CANONICAL_STAGE_ORDER: tuple[str, ...] = (
    "research",
    "plan",
    "benchmark",
    "run",
    "analysis",
    "draft",
    "review",
    "submission",
)


def _checklist(*items: ChecklistItem) -> tuple[ChecklistItem, ...]:
    return tuple(items)


STAGE_CHECKLISTS: dict[str, tuple[ChecklistItem, ...]] = {
    "research": _checklist(
        ChecklistItem(
            id="research.literature",
            statement=(
                "The canonical literature ledger covers the claims the project "
                "actually depends on: the nearest competing methods, the relevant "
                "lineage/classic anchors, contradictory or negative evidence, and "
                "the unresolved frontier. Each retained source has a verifiable "
                "primary URL and a project-relevant implication. Judge connected "
                "claim coverage, not a fixed paper or query count."
            ),
            evidence_hint=(
                "research/LITERATURE_GROUNDING.json (canonical); "
                "research/LIT_MATRIX.tsv is generated with "
                "`python -m argus_skill.verticals.research.literature_ledger sync`"
            ),
        ),
        ChecklistItem(
            id="research.brief",
            statement=(
                "A research brief frames the problem, the gap in prior work, and "
                "the proposed direction in citation-grounded prose."
            ),
            evidence_hint="research/RESEARCH_BRIEF.md",
        ),
        ChecklistItem(
            id="research.thesis",
            statement=(
                "The project states why its proposed thesis would matter to the "
                "target community, what evidence could falsify it, and whether it is "
                "worth the experiment budget. A paper-shaped deliverable is not itself "
                "a reason to continue."
            ),
            evidence_hint="research/RESEARCH_BRIEF.md and the primary sources it cites",
        ),
        ChecklistItem(
            id="research.signal_derisk",
            statement=(
                "Before leaving research, the locked idea survives the cheapest REAL "
                "falsification probe that tests its binding premise on this machine. "
                "The Planner authors the evidence contract for the research shape: a "
                "comparative method may use measured baseline/proposed deltas; a "
                "systems or architecture idea may test fidelity plus the claimed "
                "resource/stability signal; theoretical or survey work uses its own "
                "decisive counterexample/coverage test. Prefer <=10 minutes / <=$1 "
                "when faithful, but do not substitute a toy proxy merely to meet that "
                "budget. Preserve commands and raw outputs. Store the outcome without "
                "turning it into a mechanical routing decision; the Planner reads it "
                "and decides what it changes. A passed wiring-only smoke does not prove "
                "the thesis. "
                "`argus_skill.skills.signal_derisk validate` is available only for "
                "the default scalar-comparison shape and never decides quality."
            ),
            evidence_hint=(
                "Planner-authored research.signal_derisk evidence paths; for the "
                "default scalar shape use research/SIGNAL_DERISK.json + raw log"
            ),
        ),
    ),
    "plan": _checklist(
        ChecklistItem(
            id="plan.experiment",
            statement=(
                "Experiment plan states the hypothesis, the proposed method, the "
                "baselines (including the strongest feasible prior work), the "
                "ablations, the metrics, the success threshold, and the compute / "
                "API budget."
            ),
            evidence_hint="research/EXPERIMENT_PLAN.md",
        ),
        ChecklistItem(
            id="plan.benchmark",
            statement=(
                "The evaluation-source and comparator plan matches the empirical "
                "domain. Every final empirical claim includes at least one "
                "appropriate public benchmark, dataset, task suite, challenge, or "
                "official evaluation release with URL, version, license/access, "
                "evaluation unit, metric, and claim tested. The number of public "
                "sources, tasks, models, and repeats is justified by the claim scope "
                "and uncertainty method rather than a universal quota. "
                "Clinical or mechanism projects instead enumerate every real public "
                "data source, comparator/control, and planned cohort, including "
                "source URL (or the prospective registry plan), license/access "
                "conditions, observed or planned scale, implementation status, and "
                "the evidence ceiling. Unimplemented cohorts must be labeled "
                "planned with task_count=0; participant visits or nights must never "
                "be relabeled as benchmark tasks."
            ),
            evidence_hint="research/BASELINE_AND_BENCHMARK_PLAN.md, experiments/BENCHMARK_PROVENANCE.json",
        ),
        ChecklistItem(
            id="plan.code_reuse",
            statement=(
                "Code-reuse plan lists every external repo we will run, fork, or "
                "extract from, with what we will reuse vs reimplement."
            ),
            evidence_hint="research/CODE_REUSE_PLAN.json or .md",
        ),
        ChecklistItem(
            id="plan.infra_choice",
            statement=(
                "If training or large-scale inference is required, a final "
                "training-infra and inference-infra choice is locked in after the "
                "idea survives research de-risk. Compare only credible candidates "
                "that materially differ for this workload; reuse previously "
                "certified framework evidence when current. Clone and inspect the "
                "chosen framework and any code-critical comparator, not an arbitrary "
                "quota. The choice must be maintained, open-source, non-trivial, and "
                "compatible with the method and resource budget; record the decisive "
                "tradeoff and one rejected alternative. Do not write a custom "
                "trainer/inference stub when a suitable maintained framework exists."
            ),
            evidence_hint=(
                "research/INFRA_CHOICE.md (short comparison + final choice) + "
                "research/EXPERIMENT_PLAN.md `## Infra` + chosen repo evidence"
            ),
        ),
    ),
    "benchmark": _checklist(
        ChecklistItem(
            id="benchmark.environment_preflight",
            statement=(
                "Before the first real evidence-producing call, the engineer ran the "
                "Environment Readiness Gate (`argus_builtin_skills/engineer/"
                "environment-readiness-gate.md`) and captured the verbatim "
                "output to `experiments/runs/<run_id>/preflight.txt`. The "
                "preflight verifies only resources the experiment actually uses: "
                "the declared project environment, required framework/compiler "
                "imports, public data access, evaluator availability, storage, and "
                "the selected compute backend. CUDA, HF caches, model weights, or API "
                "routes are required only when the run uses them. No applicable "
                "preflight evidence means downstream benchmark items remain open."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt",
        ),
        ChecklistItem(
            id="benchmark.tasks",
            statement=(
                "Each selected public evidence source has a reproducible loader or "
                "retrieval path and its official labels, outcomes, evaluator, or "
                "analysis semantics. Locally generated diagnostics are clearly "
                "separated and never presented as the public benchmark."
            ),
            evidence_hint="public benchmark/data manifest + loader/evaluator",
        ),
        ChecklistItem(
            id="benchmark.provenance",
            statement=(
                "One canonical machine-readable benchmark provenance record lists "
                "every selected public source with "
                "version/date, license/access, split or cohort, evaluation unit, "
                "metric/evaluator, filtering, claim tested, and execution-readiness. "
                "Coverage breadth is justified by the claim. Markdown may be a "
                "generated view, but duplicate prose is not a separate gate."
            ),
            evidence_hint="experiments/BENCHMARK_PROVENANCE.json (canonical)",
        ),
        ChecklistItem(
            id="benchmark.smoke",
            statement=(
                "A faithful smoke run produced real evidence through each distinct "
                "evaluation path that the main experiment depends on."
            ),
            evidence_hint="experiments/**/smoke/*.jsonl",
        ),
        ChecklistItem(
            id="benchmark.evaluator_authentic",
            statement=(
                "The evaluation or analysis implementation is authentic for the "
                "project's empirical domain, not a stub or success-shaped oracle. "
                "For computational benchmarks, inspect that it loads actual outputs "
                "and calls the official scorer/metric rather than returning a "
                "constant. For clinical or mechanism projects, inspect that the "
                "pipeline loads the cited public source records, constructs the "
                "prespecified observation-level outcome, retains exclusions and "
                "failures, and computes the reported estimate and uncertainty from "
                "those records. Never invent an evaluator, gold label, participant, "
                "visit, or task merely to satisfy this item."
            ),
            evidence_hint=(
                "computational: evaluator source + official scorer outputs; "
                "clinical/mechanism: public-source loader/analysis code + derived "
                "rows + machine-readable result and uncertainty"
            ),
        ),
    ),
    "run": _checklist(
        ChecklistItem(
            id="run.environment_preflight",
            statement=(
                "Each pilot/full/ablation launch has a fresh, run-specific "
                "Environment Readiness Gate transcript. Verify only resources that "
                "run actually uses (environment, data/evaluator, storage, GPU/model/API "
                "as applicable); an applicable failed or missing preflight means the "
                "run is uncertified."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt (per run)",
        ),
        ChecklistItem(
            id="run.manifests",
            statement=(
                "Each long-running experiment writes manifest.json, status.json, "
                "progress.jsonl, raw scored rows, and obeys the STOP-file "
                "cancellation contract."
            ),
            evidence_hint="experiments/<run>/{manifest,status}.json + progress.jsonl + raw rows",
        ),
        ChecklistItem(
            id="run.matrix",
            statement=(
                "The proposed contribution, strongest relevant comparisons, and "
                "claim-critical controls/ablations have completed on the selected "
                "public evidence sources, or have explicit evidence-backed "
                "exclusions."
            ),
            evidence_hint="experiments/**/scored.jsonl across all method × family cells",
        ),
        ChecklistItem(
            id="run.scale",
            statement=(
                "Final empirical claims include executed public benchmark/data "
                "evidence at a scale justified by the claim and uncertainty method. "
                "Synthetic/generated diagnostics are labeled supplementary and are "
                "not the sole final evidence."
            ),
            evidence_hint="experiments/**/manifest.json declares scale=full",
        ),
        ChecklistItem(
            id="run.score_variance",
            statement=(
                "Spot-check scored rows per evidence family. A file with >3 rows "
                "and one identical score throughout is stub evidence unless the "
                "official task genuinely permits that outcome; require the authentic "
                "scorer before completing run."
            ),
            evidence_hint=(
                "`jq -r .score experiments/runs/<id>/results/<family>/scored_rows.jsonl"
                " | sort -u | wc -l` should be > 1 per file with >3 rows"
            ),
        ),
        ChecklistItem(
            id="run.method_diagnosis_recall",
            statement=(
                "Before calling an underperforming idea a scientific failure, audit "
                "whether the implementation is faithful and competitive: compare "
                "against trusted reference behavior, inspect actual executed knobs "
                "and loaded checkpoint identity/capability when relevant, inspect "
                "evaluator semantics, diagnose optimization/tuning/capacity/data "
                "limits, and test concrete plausible repairs when their information "
                "gain justifies the cost. Classify the outcome as misconfigured, "
                "under-engineered, genuine method failure, or infeasible. Do not use "
                "a fixed retry count, generic extra scale, or passing unit tests as a "
                "substitute for this diagnosis."
            ),
            evidence_hint=(
                "reference reproduction + implementation source + executed manifests "
                "+ diagnostics + targeted repair results; use the matched diagnosis skill"
            ),
        ),
    ),
    "analysis": _checklist(
        ChecklistItem(
            id="analysis.claims",
            statement=(
                "Every quantified claim the paper will make is bound to its "
                "supporting raw evidence rows and to the figure / table that will "
                "show it."
            ),
            evidence_hint=(
                "paper/claims_to_evidence.tsv + result tables/figures + canonical "
                "raw outputs"
            ),
        ),
        ChecklistItem(
            id="analysis.report",
            statement=(
                "Results report summarizes headline numbers, statistical tests / "
                "confidence intervals, ablation findings, and failure analysis "
                "with numbers grounded in raw experiment files."
            ),
            evidence_hint="paper/RESULTS_REPORT.md",
        ),
        ChecklistItem(
            id="analysis.gaps",
            statement=(
                "Known evidence gaps are explicitly enumerated with a planned "
                "supplement, ablation, or claim downgrade — no missing evidence "
                "is silently absorbed."
            ),
            evidence_hint="paper/main.tex limitations + Reviewer notes + raw results",
        ),
        ChecklistItem(
            id="analysis.thesis",
            statement=(
                "The evidence supports one defensible, venue-relevant thesis. Internal "
                "records preserve all valid outcomes, but the proposed paper is a "
                "selective argument: claim-critical contrary evidence remains visible; "
                "misconfigured runs, exploratory dead ends, and secondary diagnostics "
                "are kept in audit artifacts or an appendix rather than dumped into "
                "the main narrative. If the original method claim failed and no "
                "standalone insight remains, return to research/plan instead of drafting."
            ),
            evidence_hint="paper/main.tex + canonical raw evidence + Reviewer judgment",
        ),
    ),
    "draft": _checklist(
        ChecklistItem(
            id="draft.tex",
            statement=(
                "paper/main.tex uses the selected venue's official structure and tells "
                "one coherent argument. The title, abstract, introduction, method, and "
                "experiments all serve the same thesis; the paper does not introduce "
                "a method as its contribution and then make that method's failure the "
                "main conclusion without an independently valuable insight."
            ),
            evidence_hint="paper/main.tex + research/VENUE_PROFILE.json + research/NARRATIVE_REPORT.md",
        ),
        ChecklistItem(
            id="draft.pdf",
            statement=(
                "paper/main.pdf compiles cleanly: no '??' citations, no undefined "
                "references, no material overflow, and no LaTeX errors. Its body and "
                "back matter obey the selected venue's actual page and format rules; "
                "do not pad a weak argument to fill a historical page quota."
            ),
            evidence_hint="paper/main.pdf + paper/main.log",
        ),
        ChecklistItem(
            id="draft.bibliography",
            statement=(
                "Every BibTeX entry is verified through a scholarly source (arXiv, "
                "ACL Anthology, DBLP, CrossRef, Semantic Scholar) — none invented "
                "or auto-completed."
            ),
            evidence_hint="paper/references.bib + verification log",
        ),
        ChecklistItem(
            id="draft.figures",
            statement=(
                "The paper's figures are clear, readable at final size, visually "
                "coherent, and attractive enough for the venue. Use a good-enough "
                "standard: minor stylistic imperfections are not blockers, and do "
                "not request repeated regeneration unless a figure is unreadable, "
                "factually wrong, visibly broken, or seriously harms the paper."
            ),
            evidence_hint=(
                "paper/main.pdf rendered pages and the actual figure files; optional "
                "FIGURE_PROVENANCE.json may help locate the source renderer"
            ),
        ),
    ),
    "review": _checklist(
        ChecklistItem(
            id="review.infrastructure",
            statement=(
                "Paper prose contains no local paths (/root/, /home/), no Argus / "
                "Codex / daemon route names, no capability vault references, no "
                "device IDs, no API keys — the manuscript is publication-clean."
            ),
            evidence_hint="grep main.tex for '/root/', 'CUDA_VISIBLE_DEVICES', 'argus-skill', 'codex', 'OPENAI_API_KEY'",
        ),
        ChecklistItem(
            id="review.placeholders",
            statement=(
                "No PLACEHOLDER / TODO / TBD / FIXME / UNVERIFIED markers in the "
                "paper body, captions, or tables."
            ),
            evidence_hint="grep -nE 'PLACEHOLDER|TODO|TBD|FIXME|UNVERIFIED' paper/main.tex",
        ),
        ChecklistItem(
            id="review.tables",
            statement=(
                "Tables are readable and organized around the paper's claims. They "
                "include every comparison needed to assess the thesis, but do not "
                "force an irrelevant cross-benchmark matrix or a universal house style."
            ),
            evidence_hint="paper/main.tex tables + canonical result artifacts",
        ),
        ChecklistItem(
            id="review.citations",
            statement=(
                "Each related-work paragraph cites the specific papers it "
                "discusses; no mega-paragraphs dumping all citations, no "
                "citations buried in the bibliography with no local discussion."
            ),
            evidence_hint="paper/main.tex Related Work section",
        ),
        ChecklistItem(
            id="review.language",
            statement=(
                "Academic prose reads like a real selected-venue paper, not generic agent "
                "output: the Abstract states problem, gap, method, evidence, and "
                "implication (no result-first opening, no validator-checklist "
                "phrasing); the Introduction grounds the gap in cited prior work, "
                "then gives the method insight, a quantified result preview, and a "
                "contribution roadmap before Related Work; the Method/Setup lets an "
                "outside reviewer identify the evaluated system, baselines, task "
                "source, metrics, evaluated model/backend, and budget; every "
                "headline claim is tied to reported evidence; no unsupported hype, "
                "template LLM openings, experiment-report narration, or repeated "
                "not-X-but-Y caveats. Limitations bound the thesis instead of becoming "
                "the paper's central message. The "
                "model-backed reviewer (academic_language_review) is advisory "
                "input — this checklist, judged by the reviewer agent, is the "
                "source of truth."
            ),
            evidence_hint="paper/main.tex Abstract/Introduction/Method + paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)",
        ),
        ChecklistItem(
            id="review.publication_value",
            statement=(
                "As a venue reviewer, identify the strongest accept argument before "
                "passing. A valid experiment, transparent failure report, or complete "
                "artifact bundle is not enough: the manuscript must deliver a clear "
                "insight, capable method/system, theorem, or genuinely surprising and "
                "decision-relevant boundary. A weak result cannot be rescued by "
                "renaming it a diagnostic."
            ),
            evidence_hint="paper/main.tex + paper/main.pdf + canonical evidence",
        ),
    ),
    "submission": _checklist(
        ChecklistItem(
            id="submission.upstream",
            statement=(
                "All upstream stage checklists (research → review) are themselves "
                "marked done by a prior reviewer round — submission readiness is "
                "not a way to retro-fix missing evidence."
            ),
            evidence_hint="research/PIPELINE_STATE.json shows every stage status=done",
        ),
        ChecklistItem(
            id="submission.readiness",
            statement=(
                "The independent Reviewer has read the current manuscript and its "
                "claim-critical sources and judges the paper ready for the selected "
                "venue. No separate assurance memo or evidence package is required."
            ),
            evidence_hint="paper/main.tex + paper/main.pdf + canonical results and sources",
        ),
        ChecklistItem(
            id="submission.package",
            statement=(
                "Final PDF, BibTeX, supplementary material, and (if required) "
                "anonymous submission packaging are present and consistent."
            ),
            evidence_hint="paper/main.pdf + paper/references.bib + paper/supplementary/",
        ),
        ChecklistItem(
            id="submission.anonymous",
            statement=(
                "The compiled PDF uses the selected venue's required author and "
                "anonymity mode without contradictory or placeholder metadata."
            ),
            evidence_hint="paper/main.tex author block + selected venue submission mode",
        ),
    ),
}


def list_stages() -> tuple[str, ...]:
    """Return the canonical stage order (research → submission)."""

    return CANONICAL_STAGE_ORDER


def get_stage_checklist(stage: str) -> tuple[ChecklistItem, ...]:
    """Return the checklist items for ``stage``; empty tuple if unknown."""

    return STAGE_CHECKLISTS.get(str(stage).strip().lower(), ())



RESEARCH_TARGET_LEVELS = ("exploratory", "publishable", "doctoral")

STAGE_ORDER = [
    "research", "plan", "benchmark", "run",
    "analysis", "draft", "review", "submission",
]

# Common check: pipeline state must be valid (includes stage ordering)
_PIPELINE_CHECK = ("Pipeline state present", "test -f research/PIPELINE_STATE.json")

# Stage → code checks (description, shell command)
STAGE_CHECKS: dict[str, list[tuple[str, str]]] = {
    "research": [
        _PIPELINE_CHECK,
        ("Research brief exists", "test -f research/RESEARCH_BRIEF.md"),
        ("Literature grounding exists", "test -f research/LITERATURE_GROUNDING.json"),
        ("BibTeX file is non-empty", "test -s paper/refs.bib"),
        # Research quality and task-specific de-risk evidence are certified by
        # the L2 reviewer against the active Planner-authored checklist below.
        # Shell checks stay structural; they must not infer a domain validator
        # from checklist prose or embed benchmark-specific answers.
    ],
    "plan": [
        _PIPELINE_CHECK,
        ("Experiment plan exists", "test -f research/EXPERIMENT_PLAN.md"),
        ("Idea rejection log exists", "test -f research/IDEA_REJECTION_LOG.md"),
        ("Code study notes exist", "test -f research/CODE_STUDY_NOTES.md"),
        ("Baseline plan exists", "test -f research/BASELINE_AND_BENCHMARK_PLAN.md"),
        # NOTE: the draft-first contract (paper/DRAFT_OUTLINE.md must be
        # filled by the end of plan stage) is enforced in-process via
        # `_plan_outline_findings`, not as a shell check. Two reasons:
        #   1. it must respect `--bounded` (a bounded survey/diagnostic
        #      mission is not expected to carry a full paper outline), and
        #      shell checks always count toward the exit code; findings
        #      flow through the M0.7 bounded downgrade.
        #   2. calling the validator in-process avoids a subprocess import
        #      dependency on whichever interpreter happens to resolve on PATH.
    ],
    "benchmark": [
        _PIPELINE_CHECK,
        (
            "Evaluation provenance exists",
            "test -f experiments/BENCHMARK_PROVENANCE.md || "
            "test -f experiments/BENCHMARK_PROVENANCE.json",
        ),
    ],
    "run": [
        _PIPELINE_CHECK,
        ("Project venv exists", "test -d .venv && test -f .venv/bin/python"),
        (
            "Results exist",
            "{python} -m argus_skill.verticals.path_evidence --project-root . "
            "--glob 'experiments/**/summary.tsv' --glob 'experiments/**/eval_results.jsonl'",
        ),
        ("Baseline reproduction recorded", "test -f research/BASELINE_REPRODUCTION.md"),
    ],
    "analysis": [
        _PIPELINE_CHECK,
        ("Results report exists", "test -f paper/RESULTS_REPORT.md"),
        ("Results table exists", "test -f paper/artifacts/results_table.tsv"),
        (
            "Figures exist",
            "{python} -m argus_skill.verticals.path_evidence --project-root . "
            "--glob 'paper/figures/*.png' --glob 'paper/figures/*.pdf' "
            "--glob 'paper/figures/*.svg'",
        ),
    ],
    "draft": [
        _PIPELINE_CHECK,
        ("main.tex exists", "test -f paper/main.tex"),
        ("PDF compiles", "test -f paper/main.pdf"),
    ],
    "review": [
        _PIPELINE_CHECK,
    ],
    "submission": [
        _PIPELINE_CHECK,
        # Accept either `ready` or `done` here. The submission stage is unique:
        # the reviewer can only flip `submission.status` from `ready` -> `done`
        # AFTER `stage_check --stage submission` passes. Requiring `done` at
        # check-time creates a tautological deadlock — observed empirically
        # on agent-multimodal-reasoning-v1 as a 15-round / 9-hour polish
        # loop where the reviewer kept rejecting because submission wasn't
        # `done`, and engineer kept failing because reviewer wouldn't verdict.
        # `ready` here means upstream stages are done and the cursor is at
        # this stage; the verdict itself is what promotes to `done`.
        (
            "Submission stage is ready or done",
            "{python} -m argus_skill.verticals.stage_state --project-root . "
            "--stage submission --allow ready --allow done",
        ),
    ],
}

# Stage → reviewer checklist, per venue.
# The reviewer agent is a codex agent with shell access in the same workdir.
# It will load the skill, read the files, and do the review itself.
#
# EMNLP and AAAI are peers: five stages (research/plan/benchmark/analysis/draft)
# are venue-neutral and shared verbatim; the three format-bearing stages
# (run/review/submission) have a NATIVE definition per venue. ``reviewer_
# checklists_for(venue)`` returns the venue's full native dict — there is no
# EMNLP-privileged path or per-string patching.
REVIEWER_CHECKLISTS_EMNLP: dict[str, tuple[str, str, list[str]]] = {
    # stage: (skill_to_load, review_instructions, files_to_read)
    "research": (
        "engineer/research-brief-to-experiment-plan.md",
        "Evaluate the research foundation on these dimensions:\n"
        "1. Problem clarity — is the research gap well-defined and grounded in literature?\n"
        "2. Lineage coverage (NOT paper count) — does the canonical literature "
        "ledger plus RESEARCH_BRIEF reconstruct the field from relevant foundations "
        "through the nearest competitors to the open frontier? A flat list is "
        "shallow regardless of how many papers it contains.\n"
        "3. Source fitness — are primary scholarly/official sources used for "
        "technical claims, with trend sources used only when they add a concrete "
        "testable signal?\n"
        "4. Trend grounding — are trend insights converted to testable research questions?\n"
        "5. Direction viability — is this a real frontier gap, not just an incremental tweak?\n"
        "6. Reference code — were related papers' official repos cloned and studied?\n"
        "7. **Source-integrity audit (HARD)** — retained literature must come from "
        "real primary URLs, not model memory. Validate the canonical ledger with "
        "`python -m argus_skill.verticals.research.literature_ledger check`. "
        "Independently refetch an entry only when its URL/provenance is missing, "
        "contradictory, implausible, or material to a disputed claim. Do not grade "
        "research quality by curl/query counts and do not repeat a source audit "
        "already certified in an earlier bounded mission without a concrete conflict.\n"
        "8. **Research de-risk audit (HARD)** — read the active "
        "`research.signal_derisk` project checklist item and audit exactly the "
        "task-specific evidence contract the Planner authored there. For the "
        "default measured-signal workflow, inspect SIGNAL_DERISK.json plus its raw "
        "log and optionally run `python -m argus_skill.skills.signal_derisk "
        "validate` as a consistency/provenance diagnostic. If the Planner replaced "
        "that item for a theorem, systems result, survey, or another research shape, "
        "follow the replacement evidence paths and reasoning directly. BLOCK when "
        "the evidence is missing, fabricated, internally inconsistent, or does not "
        "satisfy the active checklist; do not require inapplicable performance "
        "metrics and do not let a task-specific Python validator decide quality.\n"
        "Pass threshold: a clear gap with claim-complete primary-source backing, "
        "not agent brainstorming or recalled papers.",
        ["research/RESEARCH_BRIEF.md", "research/LITERATURE_GROUNDING.json",
         "research/CHECKLISTS.json", "research/SIGNAL_DERISK.json",
         "research/SIGNAL_DERISK_LOG.txt"],
    ),
    "plan": (
        "reviewer/experiment-plan-review.md",
        "Evaluate the experiment plan on these dimensions:\n"
        "1. **Research taste** — does this have a genuine insight/surprising angle, not just 'applied A to B'?\n"
        "2. Method competitiveness — is the proposed method strong enough vs SOTA?\n"
        "3. Idea novelty — is this a real gap, not a manufactured/incremental one? Check IDEA_REJECTION_LOG.md\n"
        "4. Baseline strength — is at least ONE baseline a reproduced published method (not just random/no-skill)?\n"
        "5. Reference code study — were top related papers' code repos cloned and studied? Check CODE_STUDY_NOTES.md\n"
        "6. Evaluation fairness — same compute/data budget for all conditions?\n"
        "7. Public evidence adequacy — does every empirical claim have an "
        "appropriate public benchmark/data/task source, with breadth and scale "
        "justified by the claim rather than a quota?\n"
        "8. Infrastructure choice — is the right training/inference framework selected?\n"
        "9. Feasibility — can this be executed with available resources?\n"
        "10. RL config sanity (RL post-training plans only) — if the method is "
        "PPO/GRPO/RLVR/DPO/reasoning-RL, is the config learnable at a glance? "
        "Group size/num_generations >=4 (never 1) for within-group contrast; a "
        "reward that varies across rollouts (not constant-by-construction) with a "
        "validated answer-extractor; max_completion_length long enough for gold "
        "answers; RL-scale LR (<< SFT) with sane KL/clip; enough steps to show "
        "learning; init/warm-start matched to the reward. BLOCK structurally "
        "unlearnable RL configs before any GPU spend (see the skill's RL "
        "post-training auto-fails).\n"
        "If research taste is missing (no insight, just engineering), BLOCK the plan.",
        ["research/EXPERIMENT_PLAN.md", "research/IDEA_REJECTION_LOG.md",
         "research/CODE_STUDY_NOTES.md", "research/BASELINE_AND_BENCHMARK_PLAN.md"],
    ),
    "benchmark": (
        "engineer/research-experiment-runner.md",
        "Evaluate empirical-evidence preparation against the active Planner-authored "
        "benchmark checklist, not an assumed machine-learning benchmark shape:\n"
        "1. Provenance — are every used public source, planned source/cohort, version, "
        "license/access condition, analysis unit, and evidence ceiling recorded?\n"
        "2. Domain fit — computational projects must justify benchmark-family/task "
        "coverage; clinical or mechanism projects must instead verify public data, "
        "comparators/controls, planned cohorts, protocol status, and ethical gates. "
        "Never relabel participants, visits, or nights as benchmark tasks.\n"
        "3. Authentic implementation — does the real loader/evaluator/statistical "
        "analysis produce observation-level rows and uncertainty from source data, "
        "without stubs, fabricated labels, or success-shaped fallbacks?\n"
        "4. Claim boundary — are executed evidence and planned experiments separated, "
        "with null/cross-zero results and unsupported claims preserved?\n"
        "5. Reproducibility — can another researcher retrieve the public source and "
        "recompute every reported result under the stated access and ethics limits?\n"
        "Pass threshold: every item in research/CHECKLISTS.json for the benchmark "
        "stage is supported by authentic local artifacts.",
        [
            "research/CHECKLISTS.json",
            "experiments/BENCHMARK_PROVENANCE.md",
            "experiments/BENCHMARK_PROVENANCE.json",
        ],
    ),
    "run": (
        "reviewer/experiment-results-review.md",
        "Evaluate the experiment results on these dimensions:\n"
        "1. Statistical support — is uncertainty handled appropriately for the "
        "data and claim, including clean null or boundary findings?\n"
        "2. Ablation fairness — does ablation isolate the claimed contribution?\n"
        "3. Effect size — are improvements meaningful, not cosmetic?\n"
        "4. Claim support — does data actually support each claim?\n"
        "5. Baseline competitiveness — are the strongest relevant comparisons fair?\n"
        "6. Completeness — are all claim-relevant conditions represented or explained?\n"
        "Before a losing method proceeds, audit implementation adequacy against "
        "reference behavior, executed configuration, evaluator semantics, and "
        "credible optimization opportunities. Preserve valid negative evidence, "
        "but proceed toward publication only when it supports a standalone "
        "venue-relevant thesis; otherwise repair or pivot. Do not use a fixed retry count.",
        ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
    ),
    "analysis": (
        "engineer/research-results-analysis-and-figures.md",
        "Evaluate the analysis artifacts on these dimensions:\n"
        "1. Results report — does RESULTS_REPORT.md accurately summarize all experiment outcomes?\n"
        "2. Results table — does results_table.tsv have all conditions × benchmarks × metrics?\n"
        "3. Claim mapping — does each claim trace back to specific experimental evidence?\n"
        "4. Figures — are figures data-driven (not placeholder) and do they communicate key findings?\n"
        "5. Consistency — do numbers in the report match raw experiment outputs?\n"
        "Pass threshold: analysis is complete, figures are generated, claims are evidence-backed.",
        ["paper/RESULTS_REPORT.md", "paper/artifacts/results_table.tsv"],
    ),
    "draft": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "DRAFT-stage progress check (lenient, not a final peer review).\n"
        "Focus on whether the draft can move forward:\n"
        "1. Are all required sections present (abstract, intro, method, experiments, results, conclusion)?\n"
        "2. Do claims have at least placeholder evidence from actual experiments?\n"
        "3. Is the overall story coherent and the narrative structure sound?\n"
        "4. Idea spine — is the draft organized around ONE central thesis, with the method and experiments visibly serving it (not scattered, unconnected results)? Flag an experiment dump with no stated insight even at this lenient stage.\n"
        "5. Are there fatal structural problems that would block progress?\n"
        "Do NOT block on: language polish, minor formatting, incomplete related work.\n"
        "Pass threshold: structure complete enough to proceed to review stage.",
        ["paper/main.tex"],
    ),
    "review": (
        "reviewer/emnlp-academic-language-review.md",
        "Evaluate the review artifacts on these dimensions:\n"
        "1. Layout review — does LAYOUT_REVIEW.json pass? Are pages well-balanced, figures readable?\n"
        "2. Academic language — does ACADEMIC_LANGUAGE_REVIEW.json pass? No hype, salesy language, or vague claims?\n"
        "3. Infrastructure leaks — does PAPER_INFRASTRUCTURE_REVIEW.json pass? No local paths, device names, or Argus/Codex references in manuscript?\n"
        "4. Citation quality — all citations author-year natbib, no dumping, no placeholders?\n"
        "5. Page budget — body ≤8 pages, conclusion on page 8, references start page 9+?\n"
        "6. Idea-centricity and honest framing — does the paper revolve around one central thesis with a stated non-trivial insight, and where a comparison underperforms, is it scoped to a supported regime with an explicit boundary analysis rather than a flat concession or a hidden/cherry-picked table? Integrity floor: all planned claim-relevant comparisons remain reported; no cherry-picking; genuine nulls go to limitations/scope; only broken or inconclusive runs may be excluded, and only with a stated reason.\n"
        "If any review artifact has unresolved major issues, block until fixed.",
        ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
         "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual EMNLP reviewer.\n"
        "Review dimensions (all must pass):\n"
        "1. Central idea and insight — one thesis the whole paper serves, with a stated non-trivial insight; method and experiments visibly serve that thesis (not an experiment dump), and the contribution is a meaningful advance — a new mechanism, insight, or a surprising negative/boundary result — not an incremental tweak.\n"
        "2. Evidence strength and honest framing — experiments convincingly support the SCOPED claim; where the method underperforms a baseline, the paper scopes the claim to the supported regime and adds a boundary analysis WITHOUT hiding any planned claim-relevant comparison (no cherry-picking; genuine nulls in limitations/scope; only broken or inconclusive runs excluded with a stated reason).\n"
        "3. Baseline quality — are comparisons against strong, relevant baselines?\n"
        "4. Writing quality — is the paper well-written and clear?\n"
        "5. Reproducibility — enough detail to reproduce results?\n"
        "6. Significance — would EMNLP reviewers find this interesting?\n"
        "7. Format compliance — ACL format, page budget, references, appendix?\n"
        "8. Claim-evidence alignment — every claim backed by specific data?\n"
        "Score 5+/10 to pass. If the paper would get Reject at EMNLP, fail it here.",
        ["paper/main.tex"],
    ),
}


# AAAI-native overrides for the three format-bearing stages. The other five
# stages (research/plan/benchmark/analysis/draft) are venue-neutral and shared
# verbatim from the EMNLP dict below.
_AAAI_STAGE_OVERRIDES: dict[str, tuple[str, str, list[str]]] = {
    "run": (
        "reviewer/experiment-results-review.md",
        "Evaluate the experiment results on these dimensions:\n"
        "1. Statistical support — is uncertainty handled appropriately for the "
        "data and claim, including clean null or boundary findings?\n"
        "2. Ablation fairness — does ablation isolate the claimed contribution?\n"
        "3. Effect size — are improvements meaningful, not cosmetic?\n"
        "4. Claim support — does data actually support each claim?\n"
        "5. Baseline competitiveness — are the strongest relevant comparisons fair?\n"
        "6. Completeness — are all claim-relevant conditions represented or explained?\n"
        "Before a losing method proceeds, audit implementation adequacy against "
        "reference behavior, executed configuration, evaluator semantics, and "
        "credible optimization opportunities. Preserve valid negative evidence, "
        "but proceed toward publication only when it supports a standalone "
        "venue-relevant thesis; otherwise repair or pivot. Do not use a fixed retry count.",
        ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
    ),
    "review": (
        "reviewer/aaai-academic-language-review.md",
        "Evaluate the review artifacts on these dimensions:\n"
        "1. Layout review — does LAYOUT_REVIEW.json pass? Are pages well-balanced, figures readable?\n"
        "2. Academic language — does ACADEMIC_LANGUAGE_REVIEW.json pass? No hype, salesy language, or vague claims?\n"
        "3. Infrastructure leaks — does PAPER_INFRASTRUCTURE_REVIEW.json pass? No local paths, device names, or Argus/Codex references in manuscript?\n"
        "4. Citation quality — all citations author-year natbib, no dumping, no placeholders?\n"
        "5. Page budget — body ≤7 pages, conclusion by page 7, references start page 8+, Reproducibility Checklist after References?\n"
        "6. Idea-centricity and honest framing — does the paper revolve around one central thesis with a stated non-trivial insight, and where a comparison underperforms, is it scoped to a supported regime with an explicit boundary analysis rather than a flat concession or a hidden/cherry-picked table? Integrity floor: all planned claim-relevant comparisons remain reported; no cherry-picking; genuine nulls go to limitations/scope; only broken or inconclusive runs may be excluded, and only with a stated reason.\n"
        "If any review artifact has unresolved major issues, block until fixed.",
        ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
         "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual AAAI reviewer.\n"
        "Review dimensions (all must pass):\n"
        "1. Central idea and insight — one thesis the whole paper serves, with a stated non-trivial insight; method and experiments visibly serve that thesis (not an experiment dump), and the contribution is a meaningful advance — a new mechanism, insight, or a surprising negative/boundary result — not an incremental tweak.\n"
        "2. Evidence strength and honest framing — experiments convincingly support the SCOPED claim; where the method underperforms a baseline, the paper scopes the claim to the supported regime and adds a boundary analysis WITHOUT hiding any planned claim-relevant comparison (no cherry-picking; genuine nulls in limitations/scope; only broken or inconclusive runs excluded with a stated reason).\n"
        "3. Baseline quality — are comparisons against strong, relevant baselines?\n"
        "4. Writing quality — is the paper well-written and clear?\n"
        "5. Reproducibility — enough detail to reproduce results?\n"
        "6. Significance — would AAAI reviewers find this interesting?\n"
        "7. Format compliance — AAAI format, page budget, references, reproducibility checklist?\n"
        "8. Claim-evidence alignment — every claim backed by specific data?\n"
        "Score 5+/10 to pass. If the paper would get Reject at AAAI, fail it here.",
        ["paper/main.tex"],
    ),
}

#: AAAI-native reviewer checklists: neutral stages shared, format stages native.
REVIEWER_CHECKLISTS_AAAI: dict[str, tuple[str, str, list[str]]] = {
    **REVIEWER_CHECKLISTS_EMNLP,
    **_AAAI_STAGE_OVERRIDES,
}

_FRONTIERS_SLEEP_STAGE_OVERRIDES: dict[str, tuple[str, str, list[str]]] = {
    "run": (
        "reviewer/experiment-results-review.md",
        "Evaluate the evidence used by this Frontiers in Sleep Hypothesis and Theory article:\n"
        "1. Executed evidence — is every original analysis, if present, produced from authentic records with uncertainty?\n"
        "2. Planned evidence — is every proposed or unimplemented study labeled as a plan, never a result?\n"
        "3. Claim boundary — are prior evidence, original analysis, interpretation, and proposed direct tests kept distinct?\n"
        "4. Statistical integrity — are null, uncertain, or crossed-zero findings reported without spin?\n"
        "5. Reproducibility — can every original analysis and planning calculation used by the article be recomputed, or is the item explicitly N/A?\n"
        "Block any fabricated participant, outcome, registration, efficacy claim, or success-shaped fallback.",
        [
            "paper/artifacts/results_summary.tsv",
            "paper/artifacts/claims_evidence.tsv",
            "experiments/",
            "research/EXPERIMENT_PLAN.md",
        ],
    ),
    "review": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "Evaluate the final Frontiers in Sleep review artifacts:\n"
        "1. Academic language — international-standard English, bounded hypothesis framing, no internal workflow prose.\n"
        "2. Layout — official Frontiers Harvard basis, single spacing, page and line numbers; Frontiers has NO fixed page limit, so judge readability rather than conference page quotas.\n"
        "3. Authorship — single-anonymized review requires real author names, affiliations, corresponding email, CRediT contributions, conflicts, and funding.\n"
        "4. AI disclosure — public, journal-compliant disclosure of technology name/version/model/source; no internal routes, daemons, or orchestration details.\n"
        "5. Figures — every visible figure is readable, coherent, factually "
        "correct, good-looking enough, and has distinct alt text. Minor aesthetic "
        "imperfections and optional metadata gaps are not blockers.\n"
        "6. Evidence — every numerical or headline claim traces to current canonical evidence; executed and planned evidence remain distinct.\n"
        "7. Idea-centricity and honest framing — does the article revolve around one central testable thesis with a stated conceptual insight, treat null or uncertain evidence honestly without spin, scope every claim to the supported evidence, and keep planned and executed evidence distinct? Do not hide evidence that was produced; genuine nulls are reported as findings.\n"
        "Block if any review artifact is stale, unavailable, or has unresolved major issues.",
        [
            "paper/main.tex",
            "paper/LAYOUT_REVIEW.json",
            "paper/ACADEMIC_LANGUAGE_REVIEW.json",
            "paper/PAPER_INFRASTRUCTURE_REVIEW.json",
            "paper/figures/",
        ],
    ),
    "submission": (
        "reviewer/academic-paper-peer-review-benchmark.md",
        "FINAL submission gate — be STRICT, evaluate as an actual Frontiers in Sleep reviewer.\n"
        "All must pass: article-type fit; one central testable thesis with a stated conceptual insight that the article is organized around; testable theoretical contribution; honest claim-evidence alignment (scope claims to the supported evidence, report null or uncertain findings without spin, keep planned and executed evidence distinct, and hide no evidence that was produced); international-standard English; Frontiers Harvard source/PDF; main text ≤12,000 words with no fixed page quota; single spacing; page and line numbers; real single-anonymized author metadata; ethics/funding/conflict/CRediT/data/AI declarations; reviewed figures with alt text; reproducibility of any original analysis; and explicit operator approval for submission/APC exposure.\n"
        "An explicitly proposed study may remain unimplemented for a Hypothesis and Theory article, but its implementation status and every planning value must remain explicit. Judge the current manuscript and source evidence directly; do not require a separate assurance packet.",
        [
            "paper/main.tex",
            "paper/main.pdf",
            "paper/FORMAT_PREFLIGHT.md",
            "paper/artifacts/claims_evidence.tsv",
        ],
    ),
}

REVIEWER_CHECKLISTS_FRONTIERS_SLEEP: dict[
    str, tuple[str, str, list[str]]
] = {
    **REVIEWER_CHECKLISTS_EMNLP,
    **_FRONTIERS_SLEEP_STAGE_OVERRIDES,
}

#: Registry: venue key -> that venue's full native reviewer checklists. Both
#: venues are peers; add an entry here to onboard a new venue.
REVIEWER_CHECKLISTS_BY_VENUE: dict[str, dict[str, tuple[str, str, list[str]]]] = {
    "EMNLP": REVIEWER_CHECKLISTS_EMNLP,
    "AAAI": REVIEWER_CHECKLISTS_AAAI,
    "FRONTIERS_SLEEP": REVIEWER_CHECKLISTS_FRONTIERS_SLEEP,
}

#: Back-compat alias for importers predating the per-venue split (EMNLP
#: default). New callers use ``reviewer_checklists_for(venue)``.
REVIEWER_CHECKLISTS = REVIEWER_CHECKLISTS_EMNLP

#: The five venue-NEUTRAL stages, shared by every venue (built-in and dynamic).
#: The three format-bearing stages (run/review/submission) are native per
#: built-in venue, or generated from a VenueProfile by build_reviewer_checklists.
_NEUTRAL_STAGES = ("research", "plan", "benchmark", "analysis", "draft")
_NEUTRAL_CHECKLISTS: dict[str, tuple[str, str, list[str]]] = {
    stage: REVIEWER_CHECKLISTS_EMNLP[stage] for stage in _NEUTRAL_STAGES
}


def build_reviewer_checklists(
    profile: object,
) -> dict[str, tuple[str, str, list[str]]]:
    """Generate a full reviewer-checklist dict for a venue that has no hand-
    written native dict (a dynamically-researched :class:`VenueProfile`).

    The five neutral stages are shared verbatim; the three format-bearing
    stages (run/review/submission) are generated from the profile's own fields
    (``reviewer_persona``, ``page_budget_line()``, ``end_matter_prose()``), so a
    NeurIPS/ICML/... paper is graded against its own venue, not EMNLP. Uses the
    venue-neutral ``academic-paper-peer-review-benchmark`` review skill (a
    dynamic venue has no bespoke academic-language-review skill).
    """
    persona = getattr(profile, "reviewer_persona", None) or "the target venue"
    budget = profile.page_budget_line()
    end_matter = profile.end_matter_prose()
    generated: dict[str, tuple[str, str, list[str]]] = {
        "run": (
            "reviewer/experiment-results-review.md",
            "Evaluate the experiment results on these dimensions:\n"
            "1. Statistical support — is uncertainty handled appropriately for the "
            "data and claim, including clean null or boundary findings?\n"
            "2. Ablation fairness — does ablation isolate the claimed contribution?\n"
            "3. Effect size — are improvements meaningful, not cosmetic?\n"
            "4. Claim support — does data actually support each claim?\n"
            "5. Baseline competitiveness — are the strongest relevant comparisons fair?\n"
            "6. Completeness — are all claim-relevant conditions represented or explained?\n"
            f"Judge the research value for {persona}. Before a losing method proceeds, "
            "audit implementation adequacy and credible optimization opportunities. "
            "Preserve valid negative evidence, but proceed toward publication only "
            "when it supports a standalone venue-relevant thesis; otherwise repair "
            "or pivot. Do not use a fixed retry count.",
            ["paper/artifacts/results_table.tsv", "paper/artifacts/significance.tsv"],
        ),
        "review": (
            "reviewer/academic-paper-peer-review-benchmark.md",
            "Evaluate the review artifacts on these dimensions:\n"
            "1. Layout review — does LAYOUT_REVIEW.json pass? Are pages well-balanced, figures readable?\n"
            "2. Academic language — does ACADEMIC_LANGUAGE_REVIEW.json pass? No hype, salesy language, or vague claims?\n"
            "3. Infrastructure leaks — does PAPER_INFRASTRUCTURE_REVIEW.json pass? No local paths, device names, or Argus/Codex references in manuscript?\n"
            "4. Citation quality — all citations author-year, no dumping, no placeholders?\n"
            f"5. Page budget — {budget}?\n"
            "6. Idea-centricity and honest framing — does the paper revolve around one central thesis with a stated non-trivial insight, and where a comparison underperforms, is it scoped to a supported regime with an explicit boundary analysis rather than a flat concession or a hidden/cherry-picked table? Integrity floor: all planned claim-relevant comparisons remain reported; no cherry-picking; genuine nulls go to limitations/scope; only broken or inconclusive runs may be excluded, and only with a stated reason.\n"
            "If any review artifact has unresolved major issues, block until fixed.",
            ["paper/LAYOUT_REVIEW.json", "paper/ACADEMIC_LANGUAGE_REVIEW.json",
             "paper/PAPER_INFRASTRUCTURE_REVIEW.json"],
        ),
        "submission": (
            "reviewer/academic-paper-peer-review-benchmark.md",
            f"FINAL submission gate — be STRICT, evaluate as an actual {persona} reviewer.\n"
            "Review dimensions (all must pass):\n"
            "1. Central idea and insight — one thesis the whole paper serves, with a stated non-trivial insight; method and experiments visibly serve that thesis (not an experiment dump), and the contribution is a meaningful advance — a new mechanism, insight, or a surprising negative/boundary result — not an incremental tweak.\n"
            "2. Evidence strength and honest framing — experiments convincingly support the SCOPED claim; where the method underperforms a baseline, the paper scopes the claim to the supported regime and adds a boundary analysis WITHOUT hiding any planned claim-relevant comparison (no cherry-picking; genuine nulls in limitations/scope; only broken or inconclusive runs excluded with a stated reason).\n"
            "3. Baseline quality — are comparisons against strong, relevant baselines?\n"
            "4. Writing quality — is the paper well-written and clear?\n"
            "5. Reproducibility — enough detail to reproduce results?\n"
            f"6. Significance — would {persona} reviewers find this interesting?\n"
            f"7. Format compliance — {persona} format ({budget}), references, {end_matter}?\n"
            "8. Claim-evidence alignment — every claim backed by specific data?\n"
            f"Score 5+/10 to pass. If the paper would get Reject at {persona}, fail it here.",
            ["paper/main.tex"],
        ),
    }
    return {**_NEUTRAL_CHECKLISTS, **generated}


def reviewer_checklists_for(venue: object) -> dict[str, tuple[str, str, list[str]]]:
    """Return the reviewer checklists for ``venue``.

    - A built-in venue key ("EMNLP"/"AAAI"/"FRONTIERS_SLEEP") -> its
      hand-written NATIVE dict.
    - A dynamic :class:`VenueProfile` (a researched venue not in the registry)
      -> checklists generated from the profile by :func:`build_reviewer_checklists`.
    - A bare unknown venue-key string with no profile -> raises, so a new venue
      is never silently graded against another venue's checklist.
    """
    key = str(getattr(venue, "key", venue)).upper()
    if key in REVIEWER_CHECKLISTS_BY_VENUE:
        return REVIEWER_CHECKLISTS_BY_VENUE[key]
    # Dynamic venue: only a real VenueProfile (with the fields we template on)
    # can be built; a bare unknown string cannot.
    if hasattr(venue, "reviewer_persona") and callable(
        getattr(venue, "page_budget_line", None)
    ):
        return build_reviewer_checklists(venue)
    raise KeyError(
        f"no reviewer checklists for venue {key!r}: pass a built-in key or a "
        "VenueProfile (dynamic venues are built from their profile)"
    )


def _resolve_checklist_venue(project_root) -> VenueProfile:
    """Resolve the venue profile for checklist rendering.

    ``project_root`` may be None (resolved from env/cwd, matching how the
    overlay locates the project). Missing or unknown venue selection propagates
    ``KeyError`` so it cannot be silently certified against unrelated rules.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    return resolve_venue_profile(Path(project_root))


VENUE_DEPENDENT_STAGES = frozenset({"draft", "review", "submission"})


def _unresolved_venue_checklist(
    header: str,
    *,
    role: str,
    error: KeyError,
) -> str:
    """Render a fail-closed venue gate without crashing prompt construction."""
    if role == "reviewer":
        instruction = (
            "Keep this item unchecked and do not return `done`; ask the engineer "
            "to resolve it in `next_action`."
        )
    else:
        instruction = (
            "Resolve this item before doing venue-specific drafting, review, or "
            "submission work."
        )
    return (
        f"{header}\n\n"
        "### venue resolution\n"
        f"- [ ] `venue.profile` — {error}. `target_venue` must name a real "
        "publication venue, not planning commentary. If no venue was specified, "
        "live-search domain-appropriate CCF-A conferences whose deadline has not "
        "passed, record the official CCF/CFP/deadline evidence in "
        "`research/VENUE_SELECTION.md`, and write `research/VENUE_PROFILE.json` "
        f"from the selected official author kit. {instruction}"
    )


def _apply_venue_to_checklist_body(body: str, venue: VenueProfile) -> str:
    """Render the generic paper floor with the selected venue's real rules."""
    persona = venue.reviewer_persona
    page_phrase = (
        (
            f"up to {venue.body_page_limit} pages, References starts on "
            f"page {venue.references_min_page} or later"
        )
        if venue.has_fixed_page_budget
        else venue.page_budget_line()
    )
    section_label = (
        f"{venue.display_name} two-column paper sections"
        if venue.layout_format_persona.startswith("two-column")
        else f"{venue.display_name} journal-article sections"
    )
    body = body.replace(
        "paper/main.tex uses the selected venue's official structure",
        f"paper/main.tex uses the official {section_label}",
    )
    body = body.replace(
        "Its body and back matter obey the selected venue's actual page and "
        "format rules",
        f"Its body and back matter obey {page_phrase}",
    )
    body = body.replace(
        "The title, abstract, introduction, method, and experiments all serve "
        "the same thesis;",
        f"Required venue end matter: {venue.draft_section_tail()}. The title, "
        "abstract, introduction, method, and experiments all serve the same thesis;",
    )
    if venue.key == "FRONTIERS_SLEEP":
        replacements = {
            (
                "paper/main.tex uses the official Frontiers in Sleep journal-article "
                "sections and tells one coherent argument."
            ): (
                "paper/main.tex uses the Frontiers in Sleep Hypothesis and "
                "Theory sections in a coherent order: one-paragraph Abstract, "
                "Introduction, subject-relevant evidence and theory subsections, "
                "discriminating tests or proposed study, Discussion, Conclusion, "
                "required declarations, and References. The article tells one "
                "coherent argument."
            ),
            (
                "Every BibTeX entry is verified through a scholarly source (arXiv, "
                "ACL Anthology, DBLP, CrossRef, Semantic Scholar) — none invented "
                "or auto-completed."
            ): (
                "Every BibTeX entry is verified through a scholarly or canonical "
                "data source (for example PubMed, Crossref, DOI resolver, or the "
                "official repository); none is invented or auto-completed."
            ),
            (
                "Paper prose contains no local paths (/root/, /home/), no Argus / "
                "Codex / daemon route names, no capability vault references, no "
                "device IDs, no API keys — the manuscript is publication-clean."
            ): (
                "Paper prose contains no local paths, credentials, capability-vault "
                "references, device IDs, private routes, daemons, or internal "
                "reviewer/engineer workflow labels. Any generative-AI use is "
                "disclosed only in the public Frontiers-required form (technology "
                "name, version, model, source, use, and author responsibility)."
            ),
            (
                "Each related-work paragraph cites the specific papers it discusses; "
                "no mega-paragraphs dumping all citations, no citations buried in "
                "the bibliography with no local discussion."
            ): (
                "Each evidence or prior-theory paragraph cites the specific papers "
                "it discusses; no citation dumping and no bibliography entries "
                "without a reader-facing role in the manuscript."
            ),
            (
                "Final PDF, BibTeX, supplementary material, and (if required) "
                "anonymous submission packaging are present and consistent."
            ): (
                "Final PDF, TEX/BibTeX sources, figures with alt text, supplementary "
                "audit material, and required single-anonymized author/declaration "
                "metadata are present and mutually consistent."
            ),
            "paper/references.bib + verification log": (
                "paper/refs.bib (or declared bibliography source) + verification log"
            ),
            "paper/main.tex table* envs + caption": (
                "paper/main.tex table environments + captions + canonical evidence"
            ),
            "paper/main.tex Related Work section": (
                "paper/main.tex evidence/prior-theory sections"
            ),
            "paper/main.tex Abstract/Introduction/Method + "
            "paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)": (
                "paper/main.tex Abstract/Introduction/theory/evidence/Discussion + "
                "paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)"
            ),
        }
        for old, new in replacements.items():
            body = body.replace(old, new)
        body = body.replace(
            "Academic prose reads like a real EMNLP paper, not generic agent output:",
            "Academic prose reads like a real Frontiers in Sleep Hypothesis and "
            "Theory article:",
        )
        body = body.replace(
            "the Method/Setup lets an outside reviewer identify the evaluated system, "
            "baselines, task source, metrics, evaluated model/backend, and budget;",
            "the body separates prior theory, original analysis, interpretation, "
            "alternatives, falsifiers, and planned work;",
        )
    generic_author_rule = (
        "The compiled PDF uses the selected venue's required author and "
        "anonymity mode without contradictory or placeholder metadata."
    )
    generic_author_evidence = (
        "paper/main.tex author block + selected venue submission mode"
    )
    if venue.requires_real_author_metadata:
        body = body.replace(
            generic_author_rule,
            "The compiled PDF and source use the real author names, affiliations, "
            "corresponding-author email, and required contribution metadata for "
            f"{venue.review_model} {venue.display_name} review; no anonymous "
            "placeholder remains.",
        )
        body = body.replace(
            generic_author_evidence,
            "paper/main.tex author/address/correspondence/contribution fields + "
            "compiled PDF metadata",
        )
    else:
        body = body.replace(
            generic_author_rule,
            f"Anonymous submission for {venue.display_name}: the compiled PDF uses "
            f"the {persona} author block without real author names, affiliations, "
            "or self-deanonymizing strings.",
        )
        body = body.replace(
            generic_author_evidence,
            f"grep paper/main.tex for '{venue.anon_author_string}' + "
            f"{venue.style_package} submission mode",
        )
    substitutions = {
        "reads like a real selected-venue paper": f"reads like a real {persona} paper",
    }
    for old, new in substitutions.items():
        body = body.replace(old, new)
    return body


def render_stage_checklist_body(
    body: str,
    *,
    project_root,
    role: str,
    stage: str,
) -> str:
    if stage not in VENUE_DEPENDENT_STAGES:
        return body
    try:
        return _apply_venue_to_checklist_body(
            body,
            _resolve_checklist_venue(project_root),
        )
    except KeyError as exc:
        return body + "\n\n" + _unresolved_venue_checklist(
            "## Venue selection",
            role=role,
            error=exc,
        )


def render_full_checklist_body(
    body: str,
    *,
    project_root,
    role: str,
) -> str:
    try:
        return _apply_venue_to_checklist_body(
            body,
            _resolve_checklist_venue(project_root),
        )
    except KeyError as exc:
        return body + "\n\n" + _unresolved_venue_checklist(
            "## Venue selection",
            role=role,
            error=exc,
        )


# ===========================================================================
# System (B) — markdown stage checklists for the research vertical
# ===========================================================================
#
# Research owns its stage order, checklist seeds, and venue-specific rendering.
CHECKLIST_STAGE_ORDER = CANONICAL_STAGE_ORDER
CHECKLIST_ITEMS = STAGE_CHECKLISTS

#: Research missions complete on the selected venue's full-paper submission gate.
completion_gate = "full_paper"

# Research proceeds through strict stage gates, but evidence reuse within those
# stages is proportional: once a Reviewer certifies a source or artifact, later
# bounded missions verify only the new claim/delta unless a concrete conflict
# reopens it. This keeps scientific integrity without repeatedly rebuilding the
# same provenance tree.
WORKFLOW_MODE = "proportional"

# Scientific implementation and experiment claims always require a fresh,
# independent Reviewer; an Engineer verifier cannot waive this review.
REQUIRE_INDEPENDENT_REVIEW = True

_REVIEWER_ENGINEERING_AUDIT = (
    "For experiment claims, inspect the relevant implementation and raw rows once, "
    "then reuse that reviewed evidence until a dependency changes. Distinguish the "
    "method result from infrastructure or evaluator failure.\n"
)


def role_banner(role: str = "engineer") -> str:
    """Add the research-specific engineering contract to Reviewer prompts."""
    return _REVIEWER_ENGINEERING_AUDIT if role == "reviewer" else ""


__all__ = [
    "STAGE_ORDER",
    "CANONICAL_STAGE_ORDER",
    "STAGE_CHECKLISTS",
    "list_stages",
    "get_stage_checklist",
    "VENUE_DEPENDENT_STAGES",
    "render_stage_checklist_body",
    "render_full_checklist_body",
    "STAGE_CHECKS",
    "REVIEWER_CHECKLISTS",
    "_PIPELINE_CHECK",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "WORKFLOW_MODE",
    "REQUIRE_INDEPENDENT_REVIEW",
    "role_banner",
    "completion_gate",
]
