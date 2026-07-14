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
                "Literature grounding lists at least 10 recent high-quality papers "
                "and at least 3 classic anchor papers, with verifiable venue/URL "
                "and a paper-relevant summary for each."
            ),
            evidence_hint="research/LITERATURE_GROUNDING.json, research/LIT_MATRIX.tsv",
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
                "Before leaving the research stage, the locked idea passed a REAL "
                "judgemental minimal experiment (<=10 min, <=$1 cheap screen) on a "
                "model/data this box can actually run: research/SIGNAL_DERISK.json "
                "records verdict=pass where proposed_metric BEATS a REPRODUCED, "
                "competitive baseline_metric by at least min_meaningful_delta in the "
                "success_direction (the method provably wins on a cheap slice or its "
                "faithful proxy — not merely that a phenomenon moves), within budget "
                "(cost_usd<=1.0, duration_s<=600), and research/SIGNAL_DERISK_LOG.txt "
                "carries the verbatim commands + raw outputs of that run. Numbers "
                "are COMPUTED from the run, never estimated. A dead result "
                "(proposed does not beat the reproduced baseline by the margin, wrong "
                "direction, a straw-man baseline, or the model cannot even exhibit "
                "the behaviour the idea needs) means PIVOT the "
                "idea and re-run the de-risk — it is NOT allowed to enter the plan "
                "stage. The reviewer may run `python -m "
                "argus_skill.skills.signal_derisk validate` as a consistency "
                "and provenance diagnostic; the reviewer, not that command's "
                "exit code, decides whether the active checklist is satisfied."
            ),
            evidence_hint=(
                "research/SIGNAL_DERISK.json (verdict=pass, non-degenerate delta in "
                "success_direction, in budget) + research/SIGNAL_DERISK_LOG.txt "
                "(real commands + raw outputs)"
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
        ChecklistItem(
            id="research.infra_shortlist",
            statement=(
                "If the project will involve gradient-based training or "
                "large-scale inference, an initial training-infra and "
                "inference-infra shortlist is recorded. Each shortlisted "
                "framework must be (a) actively maintained (last release or "
                "default-branch commit in 2026 or later), (b) a real, "
                "non-trivial open-source repository — not a snippet, gist, "
                "or 'starter template'; framework selection is a *find*, "
                "not a *write*, exercise, and (c) verified by actually "
                "cloning the repo under `code/references/<repo>/` and "
                "reading its `README` / `docs/` / example scripts so the "
                "shortlist rationale reflects how the framework is meant "
                "to be used. **Critically, the README must be scanned for "
                "supersession / migration hints** — phrases like \"now "
                "supported by X\", \"moved to X\", \"superseded by X\", "
                "\"recommended\", \"upstreamed into X\", \"deprecated, use X\", "
                "\"this repo is archived\", or a top-of-readme note pointing "
                "at a successor project. If such a hint exists, the "
                "shortlist row must (i) add the named successor as its "
                "own candidate, (ii) compare the two in the rationale, "
                "and (iii) usually pick the successor unless there is a "
                "concrete reason to stay on the older repo (e.g. the "
                "successor does not yet support the specific algorithm "
                "this project needs). The shortlist must anchor against "
                "the bundled `argus_builtin_skills/engineer/training-"
                "infrastructure-guide.md`, add at least one candidate the "
                "agent independently discovered, and note any guide entry "
                "that turned out stale and must be excluded."
            ),
            evidence_hint=(
                "research/INFRA_SHORTLIST.md (cites URL + last-commit-date + "
                "paper if any + a 1-line note on whether the README points "
                "at a successor) plus the actual cloned repos under "
                "`code/references/`"
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
                "domain. Computational benchmark projects name at least 3 "
                "independent real benchmark families (not 3 splits of the same "
                "dataset), with URL, license, task count, and capability tested. "
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
                "training-infra and inference-infra choice is locked in (one "
                "framework per axis, picked from research.infra_shortlist) with "
                "an explicit rationale tying each choice to the project's domain "
                "(e.g. diffusion RL post-training vs LLM SFT vs agent RL) and to "
                "the resource budget. The chosen frameworks must be 2026+-active, "
                "open-source, **actually cloned and readme-studied**, and "
                "explicitly NOT a self-written stub or starter template. Cite "
                "the chosen project's repo URL, last release/commit date, and "
                "(if from a paper) the paper. Record both the final choice and "
                "any explicitly-rejected alternative with a one-line reason."
            ),
            evidence_hint=(
                "research/INFRA_CHOICE.md + research/EXPERIMENT_PLAN.md "
                "`## Infra` section + the actually-cloned framework repo under "
                "`code/references/<chosen-framework>/`"
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
                "Before the first real scoring call, the engineer ran the "
                "Environment Readiness Gate (`argus_builtin_skills/engineer/"
                "environment-readiness-gate.md`) and captured the verbatim "
                "output to `experiments/runs/<run_id>/preflight.txt`. The "
                "preflight must show: project `.venv` active (NOT the Argus "
                "framework venv), `CUDA_VISIBLE_DEVICES` matches the vault, "
                "every chosen-framework `import` succeeds, `torch.cuda.is_"
                "available()` is True, HF/Torch cache env vars point under "
                "`<project>/models/`, the base model weights are on disk, "
                "and any reward/scoring API route returns a non-empty test "
                "response. No preflight evidence ⇒ this item fails ⇒ no "
                "downstream benchmark item can be ticked."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt",
        ),
        ChecklistItem(
            id="benchmark.tasks",
            statement=(
                "Each selected benchmark family has runnable task files prepared "
                "with their official gold answers (not hand-written placeholders) "
                "and a deterministic loader."
            ),
            evidence_hint="benchmarks/<family>/tasks.jsonl + loader",
        ),
        ChecklistItem(
            id="benchmark.provenance",
            statement=(
                "Benchmark provenance lists ≥3 independent real benchmark families "
                "with version/date, license, split, task count, evaluation harness, "
                "and an execution-readiness status."
            ),
            evidence_hint="experiments/BENCHMARK_PROVENANCE.md and .json",
        ),
        ChecklistItem(
            id="benchmark.smoke",
            statement=(
                "A small smoke run produced at least one real scored row per "
                "benchmark family so the eval harness is known to work end-to-end."
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
                "Every pilot / full / ablation launch is preceded by a fresh "
                "Environment Readiness Gate run (`argus_builtin_skills/"
                "engineer/environment-readiness-gate.md`). The verbatim "
                "preflight output is captured to `experiments/runs/<run_id>/"
                "preflight.txt` for THAT run_id — not reused from an earlier "
                "run. Required signals: project `.venv` active, "
                "`CUDA_VISIBLE_DEVICES` matches the vault, framework imports "
                "succeed, `torch.cuda.is_available()` True, HF/Torch caches "
                "rooted under `<project>/models/`, base model weights present, "
                "API routes test-called. A run launched without a fresh "
                "preflight is treated as wasted budget and rolled back."
            ),
            evidence_hint="experiments/runs/<run_id>/preflight.txt (per run)",
        ),
        ChecklistItem(
            id="run.model_instruct_not_base",
            statement=(
                "Every method/baseline that must follow prompts, format answers, "
                "or do reasoning RL/eval runs on an INSTRUCTION-TUNED checkpoint "
                "(`-Instruct`/`-Chat`/`-IT`), not the same-size base/pretrained "
                "model — e.g. `Qwen3.5-9B-Instruct`, not `Qwen3.5-9B-base`. A base "
                "checkpoint has no instruction-following prior, so near-chance or "
                "format-collapsed outputs read as a dead method when the real "
                "cause is the wrong model. The manifest's declared model id and "
                "the preflight weights path must name the instruct variant. A base "
                "model is acceptable only when the experiment is explicitly about "
                "base-model behaviour and says so; otherwise fail this item."
            ),
            evidence_hint="experiments/<run>/manifest.json model id ends in an instruct/chat variant",
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
                "Proposed method, the strongest feasible literature baseline, and "
                "all planned ablations have completed on every selected benchmark "
                "family — not just one slice."
            ),
            evidence_hint="experiments/**/scored.jsonl across all method × family cells",
        ),
        ChecklistItem(
            id="run.scale",
            statement=(
                "Results are full-scale evidence, not pilot or synthetic — every "
                "row labelled as final is from a real benchmark execution."
            ),
            evidence_hint="experiments/**/manifest.json declares scale=full",
        ),
        ChecklistItem(
            id="run.score_variance",
            statement=(
                "Scored rows reflect a real scoring distribution. The reviewer "
                "must spot-check at least one `scored_rows.jsonl` per benchmark "
                "family and confirm that the `score` column varies across rows. "
                "A file with >3 rows whose scores are all identical (e.g. all "
                "1.0 or all 0.0) is treated as stub evidence — fail this item "
                "and require the engineer to wire in the real scorer (see "
                "`benchmark.evaluator_authentic`) before declaring the run "
                "stage done."
            ),
            evidence_hint=(
                "`jq -r .score experiments/runs/<id>/results/<family>/scored_rows.jsonl"
                " | sort -u | wc -l` should be > 1 per file with >3 rows"
            ),
        ),
        ChecklistItem(
            id="run.method_diagnosis_recall",
            statement=(
                "Before declaring any method DEAD on an underperformance / no-go "
                "result, attribute the cause from the EXECUTED run's own manifest "
                "+ diagnostics, NOT from the fact that it matched the plan (a plan "
                "can itself be underpowered). Identify the method family from the "
                "plan/manifest; if a method-specific failure-mode skill exists for "
                "it (a matched or known `*-diagnosis` / `*-collapse` playbook — "
                "e.g. for RL/preference post-training, "
                "`rl-training-collapse-diagnosis`), CONSULT it before configuring "
                "the run AND before judging the result, and apply ITS signatures "
                "and thresholds — do not re-derive them here. Classify the "
                "outcome as exactly one of `misconfigured_run` (re-run with the "
                "correction the skill names; do NOT record the idea as dead), "
                "`method_failure` (one fair, well-configured run STILL lost — the "
                "method may be retired), or `infeasible_under_budget` (a fair "
                "regime is unreachable within compute/budget; not an experimental "
                "refutation of the idea). Do not demand endless reruns: once one "
                "fair run exists, or the regime is infeasible, let the verdict "
                "stand, and never authorize another rerun without a named, "
                "artifact-backed diagnosis (generic 'more scale' is not one). "
                "N/A when no method-specific diagnosis skill applies."
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
                "The MIRROR of method_diagnosis_recall, guarding the opposite "
                "error: before citing a high / rising / flat / stable reward (or "
                "any metric trend) as evidence that a run is healthy / "
                "learning / successful, JUSTIFY that inference — do not treat a "
                "good-looking reward as success by default. The reviewer must "
                "rule out the invalid explanations for the signal using the "
                "matched `*-diagnosis` / `*-collapse` skill (for RL/preference "
                "post-training, `rl-training-collapse-diagnosis`) and the "
                "harness's advisory run-health signals: memorisation of a tiny "
                "admitted / curriculum-repeated set (check the DISTINCT-TASK "
                "count, not just the reward level), reward-ceiling saturation, "
                "zero-advantage / zero-variance collapse, a buffer-diluted "
                "variance metric that only LOOKS healthy "
                "(`variance_metric_masks_saturation`), evaluator leakage, and "
                "reward hacking. Treat advisory tokens such as "
                "`low_task_diversity`, `reward_ceiling_saturation`, "
                "`variance_metric_masks_saturation`, `zero_advantage` as facts "
                "to ADDRESS with evidence, NOT as automatic verdicts. You MAY "
                "mark this satisfied for a legitimately easy / converged run or "
                "an intentionally tiny / smoke / memorisation-bounded run, but "
                "ONLY with evidence that NARROWS the claim accordingly — e.g. "
                "held-out / generalisation evidence, distinct-task coverage "
                "sufficient for the stated claim, non-saturated "
                "advantage/variance, or an explicit statement that the result "
                "shows only 'solves this fixed small set', not general learning. "
                "An unqualified 'healthy / converged' verdict on a saturated, "
                "memorised, or reward-hacked run is NOT satisfied. N/A when no "
                "metric trend is being used as evidence of learning/success "
                "(e.g. a pure infra/wiring probe), or no method-specific "
                "diagnosis skill applies."
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
                "GPU-bound training / inference runs must actually saturate the "
                "allocated cards per the Hardware saturation contract in "
                "`argus_builtin_skills/engineer/training-infrastructure-guide.md`. "
                "Two things are required: (1) the run RECORDS real hardware "
                "telemetry — peak VRAM per GPU, observed GPU util%, and throughput "
                "(step time or samples/sec) — in its manifest/status/progress, not "
                "left null or absent; and (2) those numbers show MEANINGFUL "
                "saturation on every allocated card — aim for ≳70% VRAM in steady "
                "state. A run that leaves allocated A100/H100-class cards "
                "persistently idle or at ≲55% VRAM (e.g. a `gpu_memory_utilization` "
                "/ batch / `num_generations` / sequence-length default left low) is "
                "wasted budget: fail this item and require the engineer to raise the "
                "saturation knobs and rerun, rather than band-aiding the generated "
                "run scripts. N/A only for API-route / no-GPU experiments, or a run "
                "explicitly bounded as a smoke/ablation that records the deliberate "
                "reason for going small."
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
                "Every full-scale (`scale=full`) training run faithfully "
                "EXECUTES the frozen research/RUN_CONTRACT.json: the run "
                "manifest cites the contract_hash, and the launched learning "
                "rate, group size / num_generations, total steps, batch size, "
                "model id, and curriculum hash match the contract — no drift. "
                "The subagent pre-launch interlock refuses a drifting or "
                "contract-less full-scale RL launch, so a run that reached GPU "
                "without a matching contract is wasted budget. Re-verify with "
                "`python -m argus_skill.skills.run_contract check-launch ...`. "
                "This is a provenance/anti-drift check, not a science verdict. "
                "N/A for non-training projects or explicitly-bounded pilots."
            ),
            evidence_hint=(
                "experiments/<run>/manifest.json contract_hash matches "
                "research/RUN_CONTRACT.json; launched knobs == contract"
            ),
        ),
        ChecklistItem(
            id="run.curriculum_feasibility_packet",
            statement=(
                "Before each full-scale run committed GPU, a FEASIBILITY "
                "PACKET was produced on the EXACT curriculum the run consumes "
                "(same content hash, post-decontamination, with the real "
                "repetition factor): it shows the distinct-task count is large "
                "vs the planned rollout volume (NOT a memorisation regime) AND "
                "a short probe was non-saturating (advantage span > 0, reward "
                "not pinned at the ceiling, within-group reward contrast "
                "present) — OR the run is explicitly labelled smoke_only and is "
                "NOT cited as general-learning evidence. This closes the gap "
                "where a readiness screen validated a different slice than the "
                "full run consumed, so the run saturated mid-flight at zero "
                "advantage. Build/check with `python -m "
                "argus_skill.skills.run_contract build-packet` then "
                "`check-launch`. N/A for non-training projects."
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
    """Return (extra items to append, {floor_item_id: [annotation, ...]}).

    Reads the project's ACTIVE harness overlay fresh (hot-reload). Fail-open: any
    error yields no overlay so the framework floor still renders. Entries are
    RE-VALIDATED on read (caps, valid op/role, no floor-id collision, length
    limits, protected-floor policy) so a hand-edited or recovered ``active.json``
    that bypassed the write-time validators still cannot corrupt or bloat the
    prompt or weaken the floor.
    """

    try:
        from . import harness_overlay as _ho
        entries = _ho.active_checklist_items(project_root, stage=stage, role=role)
    except Exception:  # noqa: BLE001 - overlay must never break prompt building
        return (), {}
    floor_ids = {it.id for items in STAGE_CHECKLISTS.values() for it in items}
    protected = _ho.PROTECTED_ITEM_IDS
    extra: list[ChecklistItem] = []
    annotations: dict[str, list[str]] = {}
    for e in entries:
        if len(extra) >= _ho.MAX_ITEMS:
            break
        op = (e.get("op") or "").strip().lower()
        if op not in _ho.VALID_OPS:
            continue
        item_id = str(e.get("id") or "").strip()
        if not item_id:
            continue
        if op == "add":
            if item_id in floor_ids:  # collision with a framework floor item
                continue
            statement = str(e.get("statement") or "").strip()[: _ho.MAX_STATEMENT_LEN]
            evidence = str(e.get("evidence_hint") or "").strip()[: _ho.MAX_STATEMENT_LEN]
            if not statement:
                continue
            extra.append(ChecklistItem(id=item_id, statement=statement, evidence_hint=evidence))
        elif op == "amend":
            if item_id not in floor_ids:
                continue
            note = str(e.get("note") or "").strip()[: _ho.MAX_STATEMENT_LEN]
            if note:
                if item_id in protected:
                    note = f"additional requirement (cannot waive this floor item): {note}"
                annotations.setdefault(item_id, []).append(note)
        elif op == "supersede":
            # Additive only: a supersede may impose a STRICTER project-specific
            # requirement, never relax the floor. Protected items are never
            # superseded. Rendered as an additional requirement, not an override.
            if item_id not in floor_ids or item_id in protected:
                continue
            stmt = str(e.get("statement") or "").strip()[: _ho.MAX_STATEMENT_LEN]
            if stmt:
                annotations.setdefault(item_id, []).append(
                    f"additional project requirement (does not relax the item above): {stmt}"
                )
    return tuple(extra), annotations


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
    overlay locates the project). A missing target venue keeps the historical
    EMNLP default; an unknown non-empty venue propagates ``KeyError`` so it
    cannot be silently certified against the wrong rules.
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
        "publication venue, not planning commentary. Choose a built-in venue or "
        "research a non-built-in venue and write `research/VENUE_PROFILE.json` "
        "from official instructions. Never grade against the EMNLP default while "
        f"this is unresolved. {instruction}"
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
    locate the project). Fails open to the research floor
    (``CANONICAL_STAGE_ORDER`` / ``STAGE_CHECKLISTS``) so vertical resolution
    never breaks prompt building and the research/paper path stays
    byte-identical (the research vertical re-exports the same objects).

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
        from .vertical_select import resolve_vertical

        mod = load_vertical(resolve_vertical(project_root), project_root=project_root)
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
        from .vertical_select import resolve_vertical

        mod = load_vertical(resolve_vertical(project_root), project_root=project_root)
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
    extra, _annotations = _overlay_for(
        stage_norm,
        (role or "reviewer").strip().lower(),
        project_root,
    )
    if extra:
        items = items + tuple(extra)
        state = ChecklistLoadState.LOADED
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
    _extra, annotations = _overlay_for(stage_norm, role_norm, project_root)
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

    if role_norm == "reviewer":
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
        overlay_present=bool(_extra or annotations),
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
        from .vertical_select import resolve_vertical

        vertical = resolve_vertical(project_root)
        if vertical_completion_gate(
            load_vertical(vertical, project_root=project_root)
        ) != "full_paper":
            return f"## Full pipeline checklist ({vertical})\n"
    except Exception:  # noqa: BLE001 — title must never break prompt building
        pass
    return "## Full pipeline checklist (final submission gate)\n"


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
        extra, annotations = _overlay_for(stage, role_norm, project_root)
        if extra or annotations:
            overlay_present = True
        items = _store_or_seed_items(project_root, vert_items, stage) + extra
        if not items:
            continue
        blocks.append(f"### {stage}\n{_render_items(items, annotations)}")
    try:
        body = _apply_venue_to_checklist_body(
            "\n\n".join(blocks), _resolve_checklist_venue(project_root)
        )
    except KeyError as exc:
        body = _unresolved_venue_checklist(
            header,
            role=role_norm,
            error=exc,
        )
    return _augment(body, role_norm, project_root, overlay_present=overlay_present)
