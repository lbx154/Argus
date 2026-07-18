"""Stage-aware checklists that the L2 reviewer verifies for each project.

This module replaces the historical CLI-based ``validate-*`` toolbelt that
was injected into engineer / reviewer / critic prompts. Validators turned
into gates the agent kept trying to defeat; checklists are written for a
human-level reviewer to read, ground in artifacts, and rule on.

The legacy validator functions under :mod:`argus_skill.skills.pipeline_contracts`
remain importable for other call sites (tooling, tests), but the
:mod:`argus_skill.life.supervisor` harness no longer uses them for
project-done detection. Whole-project completion is now decided by the L2
reviewer's full-pipeline checklist verdict (a certified ``final_submission``
review), not by any hardcoded validator gate. The only thing the agent sees
is the markdown checklist for the current stage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .venue_profiles import VenueProfile, resolve_venue_profile

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


@dataclass(frozen=True)
class ChecklistItem:
    """One verifiable item on a stage checklist."""

    id: str
    statement: str
    evidence_hint: str


class ChecklistLoadState(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    NOT_LOADED = "not_loaded"
    EMPTY = "empty"
    LOADED = "loaded"


@dataclass(frozen=True)
class StageChecklistContract:
    stage: str
    state: ChecklistLoadState
    checklist_optional: bool
    items: tuple[ChecklistItem, ...]


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
            id="research.rejection",
            statement=(
                "At least one mediocre / already-done idea is explicitly rejected "
                "with reasoning, not silently skipped."
            ),
            evidence_hint="research/IDEA_REJECTION_LOG.md",
        ),
        ChecklistItem(
            id="research.go_no_go",
            statement=(
                "A GO / NO-GO verdict is written for whether this thesis is worth "
                "the experiment budget, with pivot criteria if conditions fail."
            ),
            evidence_hint="research/GO_NO_GO.md",
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
                "budget. Preserve commands and raw outputs. A failed necessary premise "
                "forces pivot; a passed wiring-only smoke does not prove the thesis. "
                "`argus_skill.skills.signal_derisk validate` is available only for "
                "the default scalar-comparison shape and never decides quality."
            ),
            evidence_hint=(
                "Planner-authored research.signal_derisk evidence paths; for the "
                "default scalar shape use research/SIGNAL_DERISK.json + raw log"
            ),
        ),
        ChecklistItem(
            id="research.references",
            statement=(
                "Reference repos that will be reused or compared against are "
                "shallow-cloned locally with origin URL and commit recorded."
            ),
            evidence_hint="code/references/<repo>/.git/config + a notes file",
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
        ChecklistItem(
            id="plan.rl_config",
            statement=(
                "If (and only if) the method is RL / preference post-training "
                "(PPO/GRPO/RLVR/DPO/reasoning-RL), the plan pins a learnable RL "
                "config a senior RL researcher would approve at a glance: group "
                "size / `num_generations` large enough for within-group reward "
                "contrast (>=4, never 1); a reward that actually varies across "
                "rollouts at the starting policy (not constant-by-construction) "
                "with a verifiable correctness signal and a validated "
                "answer-extractor; `max_completion_length` set as large as the "
                "context/compute budget allows (truncation kills the reward; "
                "over-length only costs throughput) and at minimum clearing the "
                "benchmark's p95 gold-answer/reasoning length with headroom (e.g. "
                "NOT 256–512 for competition-math/`\\boxed{}` reasoning); an "
                "RL-scale learning rate (<< SFT) "
                "with sane KL/clip; enough steps to show learning (not just a "
                "smoke); and an init/warm-start matched to the reward (no "
                "cold-start format RL on a bare base model). If the plan claims "
                "RL LEARNING / GENERALISATION, the admitted training set + "
                "curriculum must carry enough DISTINCT-TASK DIVERSITY to make "
                "that claim meaningful: a set so small or so repeated that a "
                "handful of distinct task ids cover all rollouts (e.g. a few "
                "admitted ids with curriculum-repeat over the same ids) is a "
                "memorisation regime, the same non-learnable class as "
                "`num_generations=1` — fail it for a learning claim. A tiny / "
                "repeated set is acceptable ONLY if the plan explicitly bounds "
                "the objective to a smoke/wiring/warmup or an avowed "
                "memorisation experiment, not general learning. N/A for non-RL "
                "plans."
            ),
            evidence_hint=(
                "research/EXPERIMENT_PLAN.md `## RL config` / training config + "
                "argus_builtin_skills/reviewer/experiment-plan-review.md "
                "(RL post-training auto-fails) + "
                "argus_builtin_skills/engineer/rl-training-collapse-diagnosis.md"
            ),
        ),
        ChecklistItem(
            id="plan.run_contract",
            statement=(
                "If the project runs training (RL / SFT / post-training), the "
                "plan freeze emits a machine-readable RUN CONTRACT "
                "(research/RUN_CONTRACT.json) that is the SINGLE SOURCE OF "
                "TRUTH for the locked launch knobs: instruct model id, learning "
                "rate, group size / num_generations, total steps, train batch "
                "size, and the curriculum (slice id + content hash + "
                "distinct-task count + seed). It carries a contract_hash over "
                "those fields so every full-scale run manifest can cite a "
                "provenance anchor. Freeze it with `python -m "
                "argus_skill.skills.run_contract freeze ...`. Without it the "
                "launch knobs drift from the plan (e.g. an LR copied from a "
                "framework reference doc) and a multi-hour run gets retired "
                "post-hoc. N/A for projects with no training run."
            ),
            evidence_hint=(
                "research/RUN_CONTRACT.json (non-empty, self-consistent "
                "contract_hash) consistent with research/EXPERIMENT_PLAN.md"
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
                "Benchmark provenance lists every selected public source with "
                "version/date, license/access, split or cohort, evaluation unit, "
                "metric/evaluator, filtering, claim tested, and execution-readiness. "
                "Coverage breadth is justified by the claim; no fixed source count "
                "or task count is imposed."
            ),
            evidence_hint="experiments/BENCHMARK_PROVENANCE.md and .json",
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
            id="run.model_instruct_not_base",
            statement=(
                "Prompt-following/reasoning methods use an instruction/post-trained "
                "checkpoint: the manifest model ID, model-card evidence, and actual "
                "checkpoint/weights path loaded in preflight must agree. A base model "
                "is allowed only for an explicitly base-model experiment. N/A for "
                "non-LLM work or tasks requiring no instruction following."
            ),
            evidence_hint=(
                "manifest model ID + model-card evidence + preflight loaded "
                "checkpoint/weights path"
            ),
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
                "Before killing an underperforming method, use its executed manifest/"
                "diagnostics and any applicable `*-diagnosis` skill. Classify exactly "
                "as `misconfigured_run`, `method_failure`, or "
                "`infeasible_under_budget`; rerun only for a named artifact-backed "
                "correction, not generic more scale. N/A when no method-specific "
                "diagnosis applies."
            ),
            evidence_hint=(
                "experiments/<run>/manifest.json executed knobs + progress.jsonl "
                "diagnostics, read THROUGH the matched method-diagnosis skill; "
                "for RL see "
                "argus_builtin_skills/engineer/rl-training-collapse-diagnosis.md"
            ),
        ),
        ChecklistItem(
            id="run.learning_validity",
            statement=(
                "Before treating a metric trend as learning, use the applicable "
                "diagnosis skill and evidence to rule out memorisation (including "
                "distinct-task coverage), saturation, zero variance/advantage, "
                "leakage, and reward hacking. Address `low_task_diversity` and "
                "`variance_metric_masks_saturation` as evidence signals, not automatic "
                "verdicts; narrow claims for intentional smoke/easy runs. N/A for "
                "pure wiring probes or when no learning claim/diagnosis applies."
            ),
            evidence_hint=(
                "experiments/<run>/progress.jsonl reward/advantage/variance "
                "series + the rl_training_health advisory signals + the "
                "distinct-task id count from reward_trace.jsonl, read THROUGH "
                "argus_builtin_skills/engineer/rl-training-collapse-diagnosis.md"
            ),
        ),
        ChecklistItem(
            id="run.gpu_saturation",
            statement=(
                "GPU runs record per-card peak VRAM, utilization, and throughput and "
                "use allocated hardware meaningfully under the training-infrastructure "
                "saturation contract. Persistently idle/low-use cards require a named "
                "bounded reason or reconfiguration. N/A for no-GPU/API work and "
                "explicit small smoke/ablations."
            ),
            evidence_hint=(
                "experiments/<run>/{manifest,status}.json or progress.jsonl record "
                "peak_vram / gpu_util% / throughput per card; cross-check "
                "`nvidia-smi` during the run shows ≳70% VRAM on allocated GPUs. "
                "See argus_builtin_skills/engineer/training-infrastructure-guide.md "
                "(Hardware saturation contract)."
            ),
        ),
        ChecklistItem(
            id="run.plan_execution_contract_match",
            statement=(
                "Every `scale=full` training launch cites the frozen RUN_CONTRACT "
                "hash and matches its model, curriculum, and launch knobs; "
                "`check-launch` must pass before GPU work. This is anti-drift "
                "provenance, not a science verdict. N/A for non-training work or "
                "explicit bounded pilots."
            ),
            evidence_hint=(
                "experiments/<run>/manifest.json contract_hash matches "
                "research/RUN_CONTRACT.json; launched knobs == contract"
            ),
        ),
        ChecklistItem(
            id="run.curriculum_feasibility_packet",
            statement=(
                "Before full training, a feasibility packet matches the exact "
                "post-decontamination curriculum hash/repetition and shows adequate "
                "distinct-task diversity plus a non-saturated real probe; otherwise "
                "label the run `smoke_only` and never cite it as general learning. "
                "Build/check through run_contract. N/A for non-training work."
            ),
            evidence_hint=(
                "feasibility packet JSON whose curriculum_hash == the run's, "
                "tied to research/RUN_CONTRACT.json"
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
            evidence_hint="paper/CLAIM_GRAPH.json + result_to_claim.tsv",
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
            evidence_hint="paper/EVIDENCE_GAPS.json",
        ),
    ),
    "draft": _checklist(
        ChecklistItem(
            id="draft.tex",
            statement=(
                "paper/main.tex exists with the EMNLP/ACL long-paper sections in "
                "the standard order (Abstract, Introduction, Related Work, Method, "
                "Experimental Setup, Results, Analysis / Ablation, Failure Cases, "
                "Conclusion, Limitations, Ethics, Reproducibility appendix)."
            ),
            evidence_hint="paper/main.tex",
        ),
        ChecklistItem(
            id="draft.pdf",
            statement=(
                "paper/main.pdf compiles cleanly: no '??' citations, no undefined "
                "references, no Overfull \\hbox > 5pt, no LaTeX errors, body is "
                "7.5-8.0 pages, References starts on page 9 or later."
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
                "At least one core conceptual figure is generated via image-2 with "
                "preserved prompt, sidecar, sha256, and inspect/review JSON; data "
                "figures are generated from raw results, not hand-drawn."
            ),
            evidence_hint="paper/figures/*.png + .json sidecars + paper/figures/IMAGE2_FIGURES.json",
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
                "Tables follow the style guide (footnotesize, tabcolsep 3-4pt, "
                "arraystretch 1.15, bold winning values) and the body has one "
                "main cross-benchmark results table (table*) covering every "
                "family × method cell."
            ),
            evidence_hint="paper/main.tex table* envs + caption",
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
                "Academic prose reads like a real EMNLP paper, not generic agent "
                "output: the Abstract states problem, gap, method, evidence, and "
                "implication (no result-first opening, no validator-checklist "
                "phrasing); the Introduction grounds the gap in cited prior work, "
                "then gives the method insight, a quantified result preview, and a "
                "contribution roadmap before Related Work; the Method/Setup lets an "
                "outside reviewer identify the evaluated system, baselines, task "
                "source, metrics, evaluated model/backend, and budget; every "
                "headline claim is tied to reported evidence; no unsupported hype, "
                "template LLM openings, or repeated not-X-but-Y caveats. The "
                "model-backed reviewer (academic_language_review) is advisory "
                "input — this checklist, judged by the reviewer agent, is the "
                "source of truth."
            ),
            evidence_hint="paper/main.tex Abstract/Introduction/Method + paper/ACADEMIC_LANGUAGE_REVIEW.json (advisory)",
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
            id="submission.assurance",
            statement=(
                "Submission assurance memo states explicit PASS / BLOCKED with "
                "named blockers and is current against paper/main.tex and the "
                "latest results."
            ),
            evidence_hint="paper/SUBMISSION_ASSURANCE.md and .json",
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
                "The compiled PDF uses the anonymous EMNLP/ACL author block (no "
                "real author names, affiliations, or self-deanonymizing strings)."
            ),
            evidence_hint="grep paper/main.tex for 'Anonymous EMNLP Submission' + acl review mode",
        ),
    ),
}


def list_stages() -> tuple[str, ...]:
    """Return the canonical stage order (research → submission)."""

    return CANONICAL_STAGE_ORDER


def get_stage_checklist(stage: str) -> tuple[ChecklistItem, ...]:
    """Return the checklist items for ``stage``; empty tuple if unknown."""

    return STAGE_CHECKLISTS.get(_normalize_stage(stage), ())


def _normalize_stage(stage: str | None) -> str:
    if not stage:
        return ""
    return str(stage).strip().lower()


def current_stage(project_root: Path | str = ".") -> str:
    """Read ``research/PIPELINE_STATE.json`` and return the current stage.

    Vertical-aware: the set of valid stages and the fallback are the ACTIVE
    vertical's ``CHECKLIST_STAGE_ORDER`` / ``CHECKLIST_ITEMS`` (research's
    canonical 8 stages by default; the speedrun vertical's 4 stages when
    selected). Falls back to the vertical's FIRST stage (``"research"`` for the
    research vertical) if the file is missing / unreadable / does not name one
    of the vertical's stages.
    """

    root = Path(project_root)
    state_path = root / "research" / "PIPELINE_STATE.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    order, items = _active_vertical_checklist_defs(project_root)
    fallback = _normalize_stage(order[0]) if order else "research"
    stage = _normalize_stage(payload.get("current_stage") if isinstance(payload, dict) else None)
    if stage in {_normalize_stage(s) for s in order}:
        return stage
    return fallback


def _set_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    by: str,
    direction: str,
    mark_current_done: bool = False,
    downgrade_downstream: bool = False,
    legacy_rollback_history: bool = False,
) -> str:
    """Single vertical-aware read-modify-write of the pipeline stage state.

    The ONE primitive behind :func:`advance_stage` and :func:`rollback_stage`.
    Resolves the active vertical's stage order + items via
    ``_active_vertical_checklist_defs`` (fails open to the research floor, so the
    research/paper path stays byte-identical), validates ``target_stage`` against
    them, then writes ``research/PIPELINE_STATE.json``:

    * ``current_stage`` -> ``target_stage``
    * if ``mark_current_done``: the *previous* stage's ``status`` -> ``done``
      (the advance case stamps the stage just completed);
    * if ``downgrade_downstream``: every stage strictly AFTER ``target_stage``
      with status in {done, ready, in_progress} -> ``pending`` (the rollback
      case, so the planner does not skip back over them);
    * appends one entry to ``stage_history`` (the unified transition log):
      ``{at, from_stage, to_stage, direction, reason, by}``;
    * if ``legacy_rollback_history``: ALSO appends the legacy ``rollback_history``
      entry (``{at, from_stage, to_stage, reason, rolled_back_by: by}``) so
      existing rollback consumers/tests stay green.

    ``direction`` is ``"advance"`` (target strictly later) or ``"rollback"``
    (target strictly earlier). Atomic write (sibling tmp file + ``os.replace``),
    ``indent=2, sort_keys=True`` + trailing newline. Raises
    ``ValueError`` on an unknown target or one that violates ``direction``.
    """
    import datetime as _dt

    root = Path(project_root)
    state_path = root / "research" / "PIPELINE_STATE.json"
    raw_order, items = _active_vertical_checklist_defs(project_root)
    order = [_normalize_stage(s) for s in raw_order]
    target = _normalize_stage(target_stage)
    # Stage EXISTENCE is governed by STAGE_ORDER, not CHECKLIST_ITEMS: a
    # Manager-authored data domain has a full stage `order` but an EMPTY items dict
    # (the Planner authors per-stage items into research/CHECKLISTS.json, which is
    # not merged here), so validating against `items` would ValueError on every
    # transition and pin the mission to stage 1 forever. Built-in verticals key
    # every stage, so order-membership == items-membership for them (unchanged).
    if target not in order:
        raise ValueError(f"unknown stage {target_stage!r}")

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    fallback_prev = order[0] if order else "research"
    previous = _normalize_stage(payload.get("current_stage") or fallback_prev)
    if previous not in order:
        previous = fallback_prev

    p_idx = order.index(previous)
    t_idx = order.index(target)
    if direction == "advance" and t_idx <= p_idx:
        raise ValueError(
            f"advance target {target!r} must be strictly later than current "
            f"stage {previous!r}"
        )
    if direction == "rollback" and t_idx >= p_idx:
        raise ValueError(
            f"rollback target {target!r} must be strictly earlier than current "
            f"stage {previous!r}"
        )
    # ``reset`` is reserved for a Manager-confirmed replacement objective. It
    # may legally land on the same first stage to clear stale completion state.

    payload["current_stage"] = target

    stages = payload.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        payload["stages"] = stages

    if mark_current_done:
        prev_record = stages.get(previous)
        if not isinstance(prev_record, dict):
            prev_record = {}
            stages[previous] = prev_record
        prev_record["status"] = "done"

    if downgrade_downstream:
        for stage_name in order[t_idx + 1:]:
            stage_record = stages.get(stage_name)
            if not isinstance(stage_record, dict):
                stage_record = {}
                stages[stage_name] = stage_record
            status = str(stage_record.get("status") or "").lower()
            if status in {"done", "ready", "in_progress"}:
                stage_record["status"] = "pending"

    # LIVENESS INVARIANT: the stage we just landed on must always be actionable.
    # A transition — most commonly a rollback ONTO an already-completed stage
    # (e.g. an open-ended reconcile rolling report -> setup while setup.status is
    # still "done") — that leaves ``current_stage`` with a terminal status
    # produces a hard deadlock: the Planner cannot dispatch work for a "done"
    # stage, and only the Manager may advance, so the mission spins forever
    # emitting ``planner_waiting``. Force the target stage back to an actionable
    # status so there is ALWAYS a legal next move. The Manager still owns the
    # DECISION of where to go (policy); the harness only guarantees the landing
    # state is workable (invariant), never overriding a still-actionable status.
    # EXCLUDES ``complete``: completing the final stage deliberately stamps it
    # ``done`` in place (project reads as complete) and must NOT be reopened.
    if direction != "complete":
        target_record = stages.get(target)
        if direction == "reset":
            if not isinstance(target_record, dict):
                target_record = {}
                stages[target] = target_record
            target_record["status"] = "in_progress"
        elif (
            isinstance(target_record, dict)
            and str(target_record.get("status") or "").lower() == "done"
        ):
            target_record["status"] = "in_progress"

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    history = payload.get("stage_history")
    if not isinstance(history, list):
        history = []
        payload["stage_history"] = history
    history.append({
        "at": now_iso,
        "from_stage": previous,
        "to_stage": target,
        "direction": direction,
        "reason": reason,
        "by": by,
    })

    if legacy_rollback_history:
        rb_history = payload.get("rollback_history")
        if not isinstance(rb_history, list):
            rb_history = []
            payload["rollback_history"] = rb_history
        rb_history.append({
            "at": now_iso,
            "from_stage": previous,
            "to_stage": target,
            "reason": reason,
            "rolled_back_by": by,
        })

    state_path.parent.mkdir(parents=True, exist_ok=True)
    # ATOMIC write: render to a sibling temp file then os.replace() (atomic on
    # POSIX). A crash mid-write (OOM / pod eviction / mission restart) must never
    # leave PIPELINE_STATE.json empty or half-written — every reader fail-opens
    # to the floor stage, silently resetting the whole project back to research.
    import os as _os
    _tmp = state_path.with_suffix(state_path.suffix + f".tmp.{_os.getpid()}")
    _tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _os.replace(_tmp, state_path)
    return str(state_path)


def advance_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    advanced_by: str = "manager",
) -> str:
    """Move the pipeline state machine **forward** to the next stage.

    ``target_stage`` must be the IMMEDIATE next stage in the active vertical's
    order (no skipping). Stamps the just-completed (previous) stage
    ``status=done`` and sets ``current_stage=target_stage``. Returns the written
    state-file path. Raises ``ValueError`` if the target is unknown or is not the
    immediate next stage.

    Post-bootstrap, ``advance_stage`` / ``rollback_stage`` are the ONLY mutators
    of ``current_stage`` — both invoked solely by the Manager, which owns stage
    authority (reviewer/planner only advise; the engineer never edits stage
    state).
    """
    raw_order, _items = _active_vertical_checklist_defs(project_root)
    order = [_normalize_stage(s) for s in raw_order]
    target = _normalize_stage(target_stage)
    if target not in order:
        raise ValueError(f"unknown stage {target_stage!r}")
    cur_norm = _normalize_stage(current_stage(project_root))
    if cur_norm in order:
        nxt_idx = order.index(cur_norm) + 1
        if nxt_idx >= len(order) or order[nxt_idx] != target:
            raise ValueError(
                f"advance target {target!r} must be the immediate next stage "
                f"after {cur_norm!r}"
            )
    return _set_stage(
        project_root,
        target_stage=target,
        reason=reason,
        by=advanced_by,
        direction="advance",
        mark_current_done=True,
    )


def rollback_stage(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    rolled_back_by: str = "reviewer",
) -> str:
    """Move the pipeline state machine **backward** to an earlier stage.

    Use this when reviewing stage N exposes a missing or unreliable
    upstream artifact (e.g. while in ``run`` the reviewer notices that
    the ``benchmark`` evaluator is a stub, or while in ``draft`` the
    reviewer notices that ``research/INFRA_CHOICE.md`` was never
    locked in). The next round will get the earlier stage's checklist
    and the agent will repair the upstream defect before being allowed
    to advance again.

    Behavior:
    - ``current_stage`` is set to ``target_stage``.
    - Every stage strictly between ``target_stage`` (exclusive) and the
      previous ``current_stage`` (inclusive) is downgraded from
      ``done``/``ready`` to ``pending`` so the planner does not skip
      back over them on the way up.
    - A ``rollback_history`` array is appended with the timestamp,
      previous stage, target stage, reason, and ``rolled_back_by`` so
      the journal carries an audit trail.

    Returns the rendered JSON file path written. Raises ``ValueError``
    if ``target_stage`` is unknown or not strictly earlier than the
    current stage.

    Thin wrapper over :func:`_set_stage` (the shared primitive): a rollback is a
    backward ``_set_stage`` that downgrades downstream stages and also appends
    the legacy ``rollback_history`` entry for back-compat. The unified
    ``stage_history`` log is written too.
    """

    return _set_stage(
        project_root,
        target_stage=target_stage,
        reason=reason,
        by=rolled_back_by,
        direction="rollback",
        downgrade_downstream=True,
        legacy_rollback_history=True,
    )


def reset_stage_for_replacement_intent(
    project_root: Path | str,
    *,
    target_stage: str,
    reason: str,
    reset_by: str = "manager",
) -> str:
    """Restart a staged pipeline for a Manager-confirmed replacement objective.

    Unlike an evidence rollback, this may target the current stage itself. The
    superseded objective's downstream statuses are downgraded and the target is
    made actionable immediately.
    """
    return _set_stage(
        project_root,
        target_stage=target_stage,
        reason=reason,
        by=reset_by,
        direction="reset",
        downgrade_downstream=True,
        legacy_rollback_history=True,
    )


def complete_final_stage(
    project_root: Path | str,
    *,
    reason: str,
    completed_by: str = "manager",
) -> str:
    """Mark the FINAL pipeline stage ``done`` without moving ``current_stage``.

    Used by the Manager when the reviewer certifies the final-submission stage:
    the project stays on its last stage (e.g. ``submission``) but that stage's
    ``status`` is stamped ``done`` so the project reads as complete. This is the
    terminal counterpart to :func:`advance_stage` / :func:`rollback_stage` and,
    like them, is a Manager-owned mutation (gated by the stage-transition
    authority context).

    Raises ``ValueError`` if ``current_stage`` is not the last stage in the
    active vertical's order (it is illegal to "complete" a non-final stage —
    advance to it first).
    """
    raw_order, _items = _active_vertical_checklist_defs(project_root)
    order = [_normalize_stage(s) for s in raw_order]
    cur = _normalize_stage(current_stage(project_root))
    if not order or cur != order[-1]:
        raise ValueError(
            f"complete target must be the final stage {order[-1] if order else '?'!r}; "
            f"current stage is {cur!r}"
        )
    return _set_stage(
        project_root,
        target_stage=cur,
        reason=reason,
        by=completed_by,
        direction="complete",
        mark_current_done=True,
    )


def _render_items(
    items: Iterable[ChecklistItem],
    annotations: dict[str, list[str]] | None = None,
) -> str:
    annotations = annotations or {}
    lines: list[str] = []
    for item in items:
        lines.append(f"- [ ] **{item.id}** — {item.statement}")
        lines.append(f"      _evidence to look at:_ `{item.evidence_hint}`")
        for note in annotations.get(item.id, ()):  # project self-authored notes
            lines.append(f"      _project note (self-authored, revertible):_ {note}")
    return "\n".join(lines)


def _overlay_for(stage: str, role: str, project_root) -> tuple[tuple[ChecklistItem, ...], dict[str, list[str]]]:
    """Compatibility shim: checklist overlays are retired.

    Framework/vertical code supplies seeds and the Planner is the sole runtime
    editor through research/CHECKLISTS.json.
    """

    _ = (stage, role, project_root)
    return (), {}
def _house_rules_block(role: str, project_root) -> str:
    """Render the project's ACTIVE self-authored house rules for ``role``."""

    try:
        from . import harness_overlay as _ho
        rules = _ho.active_prompt_rules(project_root, role=role)
    except Exception:  # noqa: BLE001
        return ""
    cleaned: list[str] = []
    for r in rules:
        text = str(r.get("text") or "").strip()[: _ho.MAX_RULE_LEN]
        if text:
            cleaned.append(text)
        if len(cleaned) >= _ho.MAX_RULES:
            break
    if not cleaned:
        return ""
    lines = ["## Project house rules (self-authored, revertible)"]
    for text in cleaned:
        lines.append(f"- {text}")
    return "\n".join(lines)


_FLOOR_STATEMENT = (
    "## Harness floor (non-negotiable)\n"
    "The project-authored items and house rules above are ADDITIVE. They may "
    "tighten but never relax the framework: they cannot waive evidence-binding, "
    "permit fabricated or placeholder results, drop variance/seed reporting, or "
    "lower the done criteria. On any conflict, the framework checklist wins."
)


def _augment(body: str, role: str, project_root, *, overlay_present: bool = False) -> str:
    """Append the house-rules block and floor statement to a rendered checklist.

    The floor statement is asserted whenever the project overlay contributed
    anything (added/annotated items or house rules), so the "additive only,
    framework wins on conflict" guardrail always accompanies self-authored edits.
    """

    house = _house_rules_block(role, project_root)
    parts = [body]
    if house:
        parts.append(house)
    if overlay_present or house:
        parts.append(_FLOOR_STATEMENT)
    return "\n\n".join(parts)


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
    """Rewrite the EMNLP-literal floor items for a non-EMNLP venue.

    The floor (``STAGE_CHECKLISTS``) is authored EMNLP-first. ``harness_overlay``
    is additive-only and cannot relax the floor, so the venue switch MUST happen
    here in the framework floor itself — otherwise an AAAI paper is failed by the
    EMNLP page-9 / "Anonymous EMNLP Submission" floor. EMNLP renders unchanged.
    """
    if venue.key == "EMNLP":
        return body
    if venue.key == "FRONTIERS_SLEEP":
        replacements = {
            (
                "paper/main.tex exists with the EMNLP/ACL long-paper sections in "
                "the standard order (Abstract, Introduction, Related Work, Method, "
                "Experimental Setup, Results, Analysis / Ablation, Failure Cases, "
                "Conclusion, Limitations, Ethics, Reproducibility appendix)."
            ): (
                "paper/main.tex exists with the Frontiers in Sleep Hypothesis and "
                "Theory sections in a coherent order: one-paragraph Abstract, "
                "Introduction, subject-relevant evidence and theory subsections, "
                "discriminating tests or proposed study, Discussion, Conclusion, "
                "required declarations, and References."
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
                "Tables follow the style guide (footnotesize, tabcolsep 3-4pt, "
                "arraystretch 1.15, bold winning values) and the body has one main "
                "cross-benchmark results table (table*) covering every family × "
                "method cell."
            ): (
                "Tables are editable, readable at normal review zoom, use concise "
                "headings and self-contained captions, and keep executed evidence, "
                "uncertainty, interpretation limits, and planned work distinct. No "
                "cross-benchmark or winner-highlighting table is required unless "
                "the manuscript actually makes comparative benchmark claims."
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
                "Academic prose reads like a real EMNLP paper, not generic agent "
                "output: the Abstract states problem, gap, method, evidence, and "
                "implication (no result-first opening, no validator-checklist "
                "phrasing); the Introduction grounds the gap in cited prior work, "
                "then gives the method insight, a quantified result preview, and a "
                "contribution roadmap before Related Work; the Method/Setup lets an "
                "outside reviewer identify the evaluated system, baselines, task "
                "source, metrics, evaluated model/backend, and budget; every headline "
                "claim is tied to reported evidence; no unsupported hype, template "
                "LLM openings, or repeated not-X-but-Y caveats. The model-backed "
                "reviewer (academic_language_review) is advisory input — this "
                "checklist, judged by the reviewer agent, is the source of truth."
            ): (
                "Academic prose reads like a real Frontiers in Sleep Hypothesis and "
                "Theory article: the single-paragraph Abstract states the problem, "
                "gap, testable hypothesis, status and uncertainty of evidence, "
                "discriminating test, and bounded implication; the Introduction "
                "grounds the gap in cited sleep/circadian work; the body separates "
                "prior theory, original analysis, interpretation, alternatives, "
                "falsifiers, and planned work; every headline claim is evidence-bound; "
                "and no unsupported efficacy, causal, priority, or treatment claim "
                "appears. The model-backed language review is advisory input; the L2 "
                "reviewer decides against this checklist."
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
    if venue.requires_real_author_metadata:
        body = body.replace(
            "The compiled PDF uses the anonymous EMNLP/ACL author block (no "
            "real author names, affiliations, or self-deanonymizing strings).",
            "The compiled PDF and source use the real author names, affiliations, "
            "corresponding-author email, and required contribution metadata for "
            f"{venue.review_model} {venue.display_name} review; no anonymous "
            "placeholder remains.",
        )
        body = body.replace(
            "grep paper/main.tex for 'Anonymous EMNLP Submission' + acl review mode",
            "paper/main.tex author/address/correspondence/contribution fields + "
            "compiled PDF metadata",
        )
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
    substitutions = {
        "EMNLP/ACL long-paper sections": section_label,
        "Conclusion, Limitations, Ethics, Reproducibility appendix": venue.draft_section_tail(),
        "7.5-8.0 pages, References starts on page 9 or later": page_phrase,
        "reads like a real EMNLP paper": f"reads like a real {persona} paper",
        "anonymous EMNLP/ACL author block": f"anonymous {persona} author block",
        "'Anonymous EMNLP Submission' + acl review mode": (
            f"'{venue.anon_author_string}' + {venue.style_package} submission mode"
        ),
    }
    for old, new in substitutions.items():
        body = body.replace(old, new)
    return body


def _active_vertical_checklist_defs(project_root):
    """Return ``(stage_order, items_dict)`` for the ACTIVE vertical.

    Resolves the active vertical via ``vertical_select.resolve_vertical`` +
    ``verticals._base.load_vertical`` and returns that vertical's
    ``CHECKLIST_STAGE_ORDER`` + ``CHECKLIST_ITEMS``. ``project_root`` may be
    None (resolved from env/cwd, matching how the overlay/venue resolution
    locate the project). An entirely undecided legacy/empty project keeps the
    historical research seed; once the Manager persists a vertical, that
    committed value is authoritative and cannot be replaced by a stale env.

    Late imports keep this free of a module-load cycle: ``stage_checklists`` is
    imported (top-level) by the vertical ``stages`` modules, so it must not
    import them at top level.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_items,
            vertical_checklist_stage_order,
        )
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS
        mod = load_vertical(vertical, project_root=project_root)
        return (
            vertical_checklist_stage_order(mod),
            vertical_checklist_items(mod),
        )
    except Exception:  # noqa: BLE001 - vertical resolution must never break prompts
        return CANONICAL_STAGE_ORDER, STAGE_CHECKLISTS


def _active_vertical_optional_stages(project_root) -> frozenset[str]:
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import (
            load_vertical,
            vertical_checklist_optional_stages,
        )
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return frozenset()
        mod = load_vertical(vertical, project_root=project_root)
        return vertical_checklist_optional_stages(mod)
    except Exception:  # noqa: BLE001
        return frozenset()


def _resolve_project_root_for_store(project_root):
    """Resolve a concrete project root for the checklist-store read (never None)."""
    import os

    if project_root is None:
        return os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    return project_root


def _store_or_seed_items(project_root, vert_items, stage):
    """Base checklist items for ``stage`` BEFORE the additive overlay.

    The per-project, Planner-authored checklist store
    (``research/CHECKLISTS.json``) is the source of truth for any stage the
    Planner has authored: when ``store_items_for_stage`` returns non-``None`` it
    REPLACES the seed for that stage (including a deliberately-emptied list). When
    it returns ``None`` (the stage is absent from the store) the active vertical's
    seed constant is used — byte-identical to the historical floor. Fail-open to
    the seed on any store error so prompt building never breaks.
    """
    try:
        from .checklist_store import store_items_for_stage

        override = store_items_for_stage(
            _resolve_project_root_for_store(project_root), stage
        )
        if override is not None:
            return tuple(override)
    except Exception:  # noqa: BLE001 — store read must never break prompt building
        pass
    return tuple(vert_items.get(stage, ()))


def resolve_stage_checklist_contract(
    stage: str,
    *,
    role: str = "reviewer",
    project_root=None,
) -> StageChecklistContract:
    """Resolve checklist provenance without treating an empty list as success."""
    stage_norm = _normalize_stage(stage)
    _stage_order, vertical_items = _active_vertical_checklist_defs(project_root)
    optional = stage_norm in _active_vertical_optional_stages(project_root)
    override = None
    try:
        from .checklist_store import store_items_for_stage

        override = store_items_for_stage(
            _resolve_project_root_for_store(project_root),
            stage_norm,
        )
    except Exception:  # noqa: BLE001
        override = None
    if override is not None:
        items = tuple(override)
        state = ChecklistLoadState.LOADED if items else ChecklistLoadState.EMPTY
    elif stage_norm in vertical_items:
        items = tuple(vertical_items.get(stage_norm, ()))
        state = ChecklistLoadState.LOADED if items else ChecklistLoadState.EMPTY
    else:
        items = ()
        state = ChecklistLoadState.NOT_LOADED
    if optional and not items:
        state = ChecklistLoadState.NOT_APPLICABLE
    return StageChecklistContract(
        stage=stage_norm,
        state=state,
        checklist_optional=optional,
        items=items,
    )


def format_stage_checklist(
    stage: str,
    *,
    role: str = "engineer",
    project_root=None,
    scope: str = "",
) -> str:
    """Render the checklist for ``stage`` as prompt-injectable markdown.

    ``role`` controls the framing line at the top:

    * ``engineer`` — "produce evidence the reviewer can tick off"
    * ``reviewer`` — "tick these items off based on artifacts you read"
    * ``critic`` / ``planner`` — "use this to decide whether more rounds add value"

    ``project_root`` locates the per-project harness overlay (``.argus/harness/``);
    when ``None`` it is resolved from ``ARGUS_SKILL_PROJECT_ROOT`` / cwd. The
    overlay is read fresh on every call so agent edits hot-reload with no daemon
    restart.

    An unknown stage or role still renders a usable block (empty body
    with a one-line note) so the caller does not need to special-case.
    """

    stage_norm = _normalize_stage(stage)
    # Vertical-aware: render the ACTIVE vertical's checklist items for this
    # stage. For the research vertical (the default) ``vert_items`` IS
    # ``STAGE_CHECKLISTS``, so the lookup — and the whole rendering below — stays
    # byte-identical to the paper pipeline. A speedrun mission instead renders
    # its own 4-stage (setup/optimize/measure/report) items.
    role_norm = (role or "engineer").strip().lower()
    contract = resolve_stage_checklist_contract(
        stage_norm,
        role=role_norm,
        project_root=project_root,
    )
    items = contract.items
    annotations: dict[str, list[str]] = {}
    if not items:
        if contract.checklist_optional:
            return (
                f"## Stage checklist ({stage_norm or 'unknown'})\n"
                "Checklist not applicable: this stage explicitly declares "
                "`checklist_optional`."
            )
        state = contract.state.value.replace("_", " ")
        return (
            f"## Stage checklist ({stage_norm or 'unknown'})\n"
            f"Configuration error: this required checklist is {state}. "
            "Do not mark the stage complete until required checklist items load."
        )

    scope_norm = (scope or "").strip().lower().replace("-", "_")
    if role_norm == "reviewer" and scope_norm == "bounded":
        framing = (
            "You are the L2 reviewer for a bounded mission. Verify the mission's "
            "explicit acceptance criteria and only the checklist items materially "
            "touched by this mission. Unrelated open items belong to later bounded "
            "missions: report them honestly, but do not use them to keep this "
            "mission running. Reply `done` when this bounded objective is satisfied; "
            "the Manager separately keeps the project stage on HOLD until every "
            "stage item is certified. Do not run any `validate-*` shell command — "
            "there isn't one. Read the relevant artifacts yourself."
        )
    elif role_norm == "reviewer":
        framing = (
            "You are the L2 reviewer. Verify each item by reading the cited "
            "evidence. Reply `continue` if any item is unmet; reply `done` only "
            "when every item is satisfied. Do not run any `validate-*` shell "
            "command — there isn't one. Read the artifacts yourself."
        )
    elif role_norm in ("critic", "planner"):
        framing = (
            "Use this checklist to judge whether another engineer round on this "
            "stage adds real value. The reviewer will rule against this list, so "
            "additional polish that does not move an unchecked item to checked "
            "is wasted budget."
        )
    else:
        framing = (
            "The L2 reviewer will tick these items against your artifacts. "
            "Produce the evidence each item names; do not look for a "
            "`validate-*` CLI — the agent surface has none. The reviewer "
            "reads files directly."
        )

    body = (
        f"## Stage checklist ({stage_norm})\n"
        f"{framing}\n\n"
        f"{_render_items(items, annotations)}"
    )
    if stage_norm in VENUE_DEPENDENT_STAGES:
        try:
            body = _apply_venue_to_checklist_body(
                body, _resolve_checklist_venue(project_root)
            )
        except KeyError as exc:
            body = _unresolved_venue_checklist(
                f"## Stage checklist ({stage_norm})",
                role=role_norm,
                error=exc,
            )
    return _augment(
        body,
        role_norm,
        project_root,
        overlay_present=False,
    )


def _full_pipeline_title(project_root) -> str:
    """Vertical-aware title line for the full-pipeline checklist header.

    Paper-shaped verticals (``completion_gate == "full_paper"``, i.e. research)
    keep the historical ``final submission gate`` wording byte-identical. Any
    other vertical (e.g. speedrun, whose gate is ``"metric"``) names itself so
    the header is not research-flavoured. Fails open to the research wording so
    title resolution never breaks prompt building.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return "## Full pipeline checklist (final submission gate)\n"
        if vertical_completion_gate(
            load_vertical(vertical, project_root=project_root)
        ) != "full_paper":
            return f"## Full pipeline checklist ({vertical})\n"
    except Exception:  # noqa: BLE001 — title must never break prompt building
        pass
    return "## Full pipeline checklist (final submission gate)\n"


def _full_pipeline_requires_venue(project_root) -> bool:
    """Return whether the active vertical is a paper/venue pipeline.

    Metric and software verticals must never be replaced by the unresolved-venue
    checklist merely because they have no ``target_venue``.  Venue resolution is
    meaningful only for ``completion_gate == "full_paper"``.
    """
    import os

    if project_root is None:
        project_root = os.environ.get("ARGUS_SKILL_PROJECT_ROOT") or "."
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate
        from .vertical_select import resolve_checklist_vertical

        vertical = resolve_checklist_vertical(project_root)
        if vertical is None:
            return True
        return (
            vertical_completion_gate(load_vertical(vertical, project_root=project_root))
            == "full_paper"
        )
    except Exception:  # noqa: BLE001 — preserve historical paper-safe fallback
        return True


def format_full_pipeline_checklist(
    *,
    role: str = "reviewer",
    project_root=None,
) -> str:
    """Render every stage's checklist concatenated, for final submission review."""

    title = _full_pipeline_title(project_root)
    role_norm = (role or "reviewer").strip().lower()
    if role_norm == "reviewer":
        header = (
            title
            + "Verify every stage's items end-to-end. Reply `done` only when every "
            "item across every stage is satisfied. There is no `validate-*` "
            "command to run — read the artifacts directly."
        )
    else:
        header = (
            title
            + "Every item below must be true before the project can be marked done."
        )

    blocks = [header]
    overlay_present = False
    # Vertical-aware: iterate the ACTIVE vertical's stage order and render its
    # items. For the research vertical (the default) ``stage_order`` IS the
    # canonical 8 stages and ``vert_items`` IS ``STAGE_CHECKLISTS``, so the
    # concatenated output stays byte-identical to the paper pipeline. A speedrun
    # mission instead concatenates its own 4 stages.
    stage_order, vert_items = _active_vertical_checklist_defs(project_root)
    for stage in stage_order:
        annotations: dict[str, list[str]] = {}
        items = _store_or_seed_items(project_root, vert_items, stage)
        if not items:
            continue
        blocks.append(f"### {stage}\n{_render_items(items, annotations)}")
    body = "\n\n".join(blocks)
    if _full_pipeline_requires_venue(project_root):
        try:
            body = _apply_venue_to_checklist_body(
                body, _resolve_checklist_venue(project_root)
            )
        except KeyError as exc:
            body = _unresolved_venue_checklist(
                header,
                role=role_norm,
                error=exc,
            )
    return _augment(body, role_norm, project_root, overlay_present=overlay_present)
