---
name: Auto Research Pipeline
description: "PRIMARY ENTRY POINT for all research projects. Orchestrates the end-to-end auto-research mission as a gated state machine: research brief → literature → experiments → analysis → paper draft → reviews → submission. All other skills are subordinate to this pipeline."
category: research-orchestration
priority: highest
version: 2
created_at: 2026-05-28T00:00:00+00:00
---

## Title
Auto Research Pipeline — Primary Orchestrator

## Description
Run a complete research project as a gated artifact workflow rather than a one-shot paper generator. This is the argus-skill-native orchestrator inspired by ARIS research-pipeline: it coordinates planning, benchmark selection, experiments, result-to-claim analysis, paper writing, and submission assurance while preserving explicit pivot/reject paths.

## Non-negotiable project bar
- The project must target a real frontier gap in the selected domain, grounded in current papers, official benchmark reports/leaderboards, and strong baselines. A local mechanism demo, synthetic proxy, or validator-shaped paper is not enough.
- **Research taste and innovation are mandatory.** This system produces RESEARCH papers, not engineering reports. Every project must have:
  - A genuine insight about WHY something works or doesn't, not just "we tried X and it improved Y"
  - A surprising finding, counter-intuitive result, or novel perspective that makes reviewers think "I hadn't considered that"
  - A contribution that advances understanding, not just numbers on a leaderboard
  - Simple reproduction of existing work is NOT a paper. Adding a trivial module to an existing system is NOT a paper. You must have a real thesis.
- If local GPUs can train or adapt a substantial domain model, the proposed method must use that capability. Prompt-only wrappers, exact-oracle policies, bag-of-words scorers, and tiny custom classifiers are allowed as smoke tests or baselines, not as the main proposed system, unless the operator explicitly changes the scope.
- Final benchmark evidence must come from existing real benchmarks or official task/data releases. Locally invented synthetic benchmarks, generated tasks, proxy graphs, and hand-written oracle tasks are forbidden as main evidence. If no real benchmark tests the idea, pivot or block instead of fabricating a benchmark.

## When to use
- The operator asks for a full autonomous research cycle, auto research, idea-to-paper, EMNLP/ACL paper generation, or a long-running research mission.
- No single-stage skill is clearly enough because the task spans literature/idea framing, experiments, analysis, writing, and audit.
- `research/PIPELINE_STATE.json` is absent or the operator asks to resume a full pipeline.

## When NOT to use
- The operator asks only to run a benchmark, analyze results, draft an existing paper, or revise an existing draft; use the narrower stage skill.
- Required credentials, datasets, or compute are missing and no local smoke alternative can establish feasibility.
- The current evidence already proves the idea should be rejected; write the rejection/pivot report instead of forcing a paper.

## ⚡ YOUR RESOURCES

Before starting work, check what the operator has allocated for you:

```python
import json, os
vault = os.path.expanduser("~/.argus-skill/capabilities")
# GPU
gpu = json.load(open(f"{vault}/gpu_resources.json"))
print("GPU devices:", gpu["cuda_visible_devices"])  # e.g. "6"
# API (for reward models, VLM, image gen)
api = json.load(open(f"{vault}/model_api.json"))
routes = api["capabilities"]["model_api"]["routes"]
print("Text API:", routes["text"]["base_url"], routes["text"]["model"])
print("API key:", routes["text"]["api_key"][:10] + "...")
```

- **GPU**: CUDA_VISIBLE_DEVICES is auto-set. Use `.venv/bin/python` for training.
- **API**: Use for reward models, VLM scoring, image quality evaluation.
- **Subagent**: GPU tasks >60s → `python -m argus_skill.tools.subagent submit --mode supervised`
- **Project venv**: `.venv/` for ML deps. See `project-environment-management` skill.

## Pipeline state contract
Create or update `research/PIPELINE_STATE.json` before doing expensive work. The file is the mission ledger, not decorative prose:

```json
{
  "current_stage": "plan",
  "objective": "short operator objective",
  "target_venue": "EMNLP",
  "stages": {
    "research": {"status": "done", "artifact": "research/RESEARCH_BRIEF.md"},
    "plan": {"status": "running", "artifact": "research/EXPERIMENT_PLAN.md"},
    "benchmark": {"status": "missing"},
    "run": {"status": "missing"},
    "analysis": {"status": "missing"},
    "draft": {"status": "missing"},
    "review": {"status": "missing"},
    "submission": {"status": "missing"}
  },
  "last_gate": {"verdict": "pending", "reason": "planning not complete"}
}
```

Allowed stage statuses are `missing`, `pending`, `ready`, `running`, `blocked`, `pivot`, `rejected`, and `done`. A stage marked `ready` or `done` must have the required artifact on disk. Use the Python contract checker in `argus_skill.skills.pipeline_contracts` when available.

## Artifact consistency contract
From the analysis stage onward, keep a single machine-checkable source of truth for paper artifacts:

- Create or repair `paper/ARTIFACT_MANIFEST.json` by running `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` after the relevant files exist. This command bootstraps a missing manifest, converts legacy bare-string entries to objects, refreshes SHA-256 digests, adds TSV columns, and fills conservative generated-artifact `sources`.
- Each manifest entry must be an object, never a bare string. It must contain a POSIX relative `path` and a lowercase SHA-256 `sha256`; TSV entries must also declare exact `columns`.
- Every generated artifact, including `paper/RESULTS_REPORT.md`, `research/NARRATIVE_REPORT.md`, `paper/main.tex`, `paper/submission/main.tex`, reports, and figures, must list `sources` that transitively reach a canonical source such as raw results or canonical TSV/JSON summaries. Do not hand-write missing `sources` unless the refresh tool cannot infer them and the L2 reviewer names the exact remaining source gap.
- Do not hand-edit numbers in generated prose or LaTeX. Update the canonical source, regenerate downstream artifacts, then run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .`.
- After regenerated artifacts are stable, run `python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .` so `paper/ARTIFACT_FRESHNESS.json` records current generated outputs and their input hashes.
- If `VALIDATION_PRIORITY_POLICY.json` is missing or reports route errors, run `python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .`; do not hand-write a partial policy.
- If final validation reports manifest, freshness, and validation-route drift together, run `python -m argus_skill.skills.pipeline_contracts repair-emnlp-contract-artifacts --project-root .` after regenerating content artifacts; then inspect any remaining issues as real content/evidence blockers.
- Before marking `analysis`, `draft`, `review`, or `submission` as `ready`/`done`, self-audit the full EMNLP submission contract across every stage checklist; the retired pipeline-contract validation gates are no-ops and the L2 reviewer verifies artifacts directly. If review finds digest drift, TSV schema drift, unknown sources, or missing manifest entries, the stage is `blocked` until regenerated. Before any final EMNLP-ready claim, also self-audit the full EMNLP submission contract across every stage checklist; stage-sensitive readiness alone is not sufficient.

## Final EMNLP completion contract
- Treat intermediate work as `bounded` scope: literature, pilot feasibility, benchmark scale-up, baseline/ablation implementation, drafting, assurance, and revision can complete on their own acceptance criteria without proving the whole paper is ready.
- Use `final_submission` scope only for the project-final task that claims the submission package is complete.
- A full EMNLP submission experiment is not complete until the full EMNLP submission contract has been self-audited across every stage checklist and that evidence is quoted in the completion report.
- The full-scale experiment sub-gate is explicit: self-audit the full-scale experiment-evidence requirement (completed raw scored rows under `experiments/**` for every required method/baseline condition) before `analysis`, `narrative`, `draft`, `assurance`, or `submission` can be marked `ready`/`done`.
- Benchmark construction is not execution. `benchmarks/full/tasks.jsonl` or a full benchmark manifest proves only that tasks were assembled; it does not satisfy run evidence until `experiments/**/results.jsonl`, `progress.jsonl`, or equivalent raw rows show completed scored trials.
- For required method/baseline matrices, every required condition must run on the same executed multi-source benchmark matrix. A completed `status.json` or declared task count is insufficient if raw result rows cover fewer tasks, omit a baseline, or collapse to one benchmark family. Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
- stage-checklist self-audits, a PDF existing on disk, or a small pilot are never sufficient final evidence.
- The minimum final evidence package must include enough benchmark coverage for the paper contract: at least 3 independent executed real benchmark families, raw scored rows for every required method/baseline condition, nontrivial baselines, ablations/failure analysis, paper-quality calibration, submission assurance, and a long-paper draft that satisfies the official ACL/EMNLP page/template contract. A small or single-source run is only pilot evidence, not final EMNLP evidence.
- Hard prohibition: the final benchmark cannot be a duplicated pilot. Do not copy, relabel, suffix (`_r2`, `_copy`, `_dup`, etc.), shuffle, or otherwise rename the same episodes/prompts/specs/gold answers to inflate task count; this is an experiment-integrity blocker even if tables report a large `n_tasks`.
- Benchmark selection must be literature-grounded: the plan/provenance must show a survey of recent/frontier, widely used public benchmarks and official repos (for example ToolBench/ToolEval, WebArena/MiniWoB++/Mind2Web, GAIA, AgentBench/ALFWorld, MultiAgentBench, SWE-bench, LoCoMo, or a domain-specific ACL benchmark) before relying on synthetic tasks.
- Final benchmark evidence must not use synthetic tasks at all. Synthetic/local tasks can only be smoke tests and must not appear in main result tables or paper claims.
- Final benchmark evidence must not be single-source, same-family-only, or planned-only. The plan/provenance must list at least 3 independent executed existing real benchmark source families as a hard minimum. For each selected source, record URL/repo, paper/citation/DOI, version/date, split/filtering, unique task count, license/access, capability/failure mode covered, execution status, and rationale for why this source belongs in the evidence mix. Planned diagnostic rows do not count until raw scored rows exist.
- Final method evidence must include a model-scale plan and training/adaptation artifacts when the contribution is a learned method: model/backbone, parameter count, trainable parameter count, training data, GPU memory plan, wall/GPU-hours, checkpoints, and exact evaluation command. A compact heuristic scorer or exact lookahead policy can be reported only as a baseline/ablation unless operator-approved as a non-frontier scope.
- The final paper must satisfy the `research.md` formatting contract, not merely compile: official ACL/EMNLP review template, anonymous author block (`Anonymous EMNLP Submission`), body visibly filled to the long-paper budget (Conclusion not before page 7 and References/Appendix not before page 9 when PDF text can be extracted), conclusion by the end of page 8 without a forced manual page break immediately before it, Limitations and Ethical Considerations present after the conclusion, References before Appendix, no total-page maximum after References/Appendix begin, and a complete reproducibility appendix.
- PDF preflight is a hard gate: no undefined references, no citation warnings, no `[?]` in rendered text, no `Overfull \hbox > 5pt`, no `\textbf{[PLACEHOLDER]}` strings, no `% UNVERIFIED` entries in `refs.bib` unless the operator explicitly accepts unresolved verification, at least 35 verified BibTeX entries, at least 30 unique cited keys, at least two rendered References pages when PDF text extraction is available, no rendered `and 1 others`/`and N others`, no BibTeX `author={... and others}` placeholders, no citation dumping, and no code-font/snake_case display labels in abstracts, headings, captions, figures, or tables unless explicitly justified.
- Figure/table preflight is a hard gate: every figure has a `\label{}` and is referenced in text, every table has a caption with a numerical headline, middle-body visual rhythm is accepted by the vision layout reviewer, and at least one paired-significance table exists when comparative binary outcomes are reported.
- Body layout must stay reviewable: <=5 body figures total, only one `figure*` full-width float, `[t]` used only for critical figures, no overlapping or interleaved floats, no unreadable tiny tables, and no table/text overflow above the 5pt overfull threshold.
- Data/metric/result plots may be generated from scripts. Every other paper-facing figure (Figure 1, teaser, overall, method/framework/system/pipeline overview, schematic, qualitative/example visual, architecture diagram, or explanatory non-data figure) must use the actual image-2/codex-image2 raster output in `paper/main.tex`. **Hard prohibition:** the engineer must not draw, redraw, trace, clean, or "improve" any non-data figure with matplotlib/FancyBboxPatch, TikZ node graphs, Inkscape/manual vector tools, screenshots, PIL/SVG/HTML canvases, or cleaned PDF derivatives. If a non-data figure is ugly, write a better image-2 prompt, generate/select/review new image-2 attempts, and replace the raster `output_path`; do not self-draw a replacement or label a local raster as `codex-image2`. Record width/height, SHA-256 hashes, generation provenance, raw image-tool `sidecar_path`, `inspect_path`, and model-backed `review_path`; pass a 4/5-or-better image review from the `image_review` route, and avoid square `1024x1024` output. Prefer adaptive or landscape academic diagrams sized for page-width reading.
- The overview/teaser prompt must be created with `python -m argus_skill.tools.image_tool paper-prompt ...`, retaining `argus-image2-paper-prompt-v1` and `paper-framework-figure-studio-pro-v3.1.4a`. It must keep `General style`, `Style intent`, `Pinned content`, `Layout variant`, `Negative prompt / Avoid`, Figma tokens, figure-caption contract, and core mechanism contract. Generate 6--20 variants by swapping only the named layout/candidate-contract fields; do not improvise a one-line prompt.
- Tables must follow the `research.md` styling tokens before final readiness: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent for meaningful degradation only, and bold winning values.
- Final submission must include a clean dedicated format preflight: invoke the EMNLP Format Preflight skill and self-audit the `research.md` format-preflight requirements before academic-language or layout review.
- Final submission must include a tool-generated academic-language review: `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write` followed by self-auditing the academic-language review thresholds. `paper/ACADEMIC_LANGUAGE_REVIEW.json` must score at least 4/5, use a model-backed reviewer with quoted evidence spans and fresh LaTeX source hashes, set `needs_revision: false`, and contain no active revision directives.
- Final submission must include a model-backed paper infrastructure review: `python -m argus_skill.skills.paper_infrastructure_review --project-root . --review-mode model --write` followed by self-auditing the paper-infrastructure review thresholds (leak_free, score). `paper/PAPER_INFRASTRUCTURE_REVIEW.json` must certify `leak_free: true` for title, abstract, body, captions, tables, and appendix prose, with fresh LaTeX source hashes and no active directives. Remove local device IDs, CUDA variables, cache paths, local filesystem paths, Argus/Codex daemon details, reviewer route labels, and paper-generation configuration from rendered prose; keep them only in non-paper manifests/logs when needed.
- Final submission must include a tool-generated visual layout review: `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write` followed by self-auditing the layout review thresholds. `paper/LAYOUT_REVIEW.json` must score at least 4/5, use rendered PDF page snapshots with fresh hashes, set `needs_revision: false`, and contain no blocking issues. The layout reviewer should focus on the body and middle-paper visual rhythm: once Conclusion is on/before page 8 and References/Appendix start on page 9 or later, natural trailing whitespace on the final References/Appendix page is not a hard blocker by itself. Treat it as advisory unless it exposes a separate defect such as unreadable tables, detached captions, missing required appendix content, or broken reference/appendix order.
- Review artifacts are generated evidence, not knobs. Do not hand-edit, normalize, or append PASS records for `paper/ACADEMIC_LANGUAGE_REVIEW.*`, `paper/PAPER_INFRASTRUCTURE_REVIEW.*`, or `paper/LAYOUT_REVIEW.*`; a top-level PASS is invalid when nested `model_review` or `vision_review` still reports revise, major/blocking issues, failed checks, low scores, leaks, or revision directives. Repair the manuscript/layout and rerun the owning review command.
- Final submission must include thick exemplar learning: invoke the Paper Exemplar PDF Learning skill, download at least two open-access top-conference exemplar PDFs under `paper/style_ref/exemplars/`, extract text, record `pdf_sha256`, write a thick `paper/style_ref/STYLE_PROFILE.md`, write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`, and self-audit the exemplar-grounding and structure-blueprint requirement. At least one exemplar should be a recent EMNLP/ACL best/outstanding/award paper when available. URL-only `EXEMPLAR.json` entries are blockers. After drafting, `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` must map every final top-level section to exemplar phases, evidence sources, exemplar lessons, and justified deviations; final readiness fails on unmapped filler sections.
- For positive paper objectives, final readiness requires `paper/PAPER_QUALITY_CALIBRATION.json.paper_contribution` with a one-sentence X-Y-Z-W claim: "We propose X. We show X improves Y by Z because W." The proposed artifact/protocol must beat the strongest nontrivial baseline on the declared primary metric, including held-out/public-validation splits when present, and the claim must cite statistical-support artifacts.
- Do not convert a failed proposed method into a negative-result paper to satisfy the gate. If smoke or main experiments reject the method-positive thesis, queue repair/pivot tasks for the method, metric, benchmark, or objective; do not mark `final_submission` done unless the operator explicitly asked for a negative-result paper.
- Standard starter citation targets for memory / agent-skills / hallucination projects include ReAct, Reflexion, SELF-REFINE, Toolformer, ToolLLM, API-Bank, Gorilla, HuggingGPT, MRKL Systems, Voyager, ExpeL, MemGPT, Generative Agents, A-Mem, MemoryBank, LongMem, WebRL, WebEvolver, Mobile-Agent-E, SAGE, SkillRL, Let's Verify Step by Step, STaR, MT-Bench/LLM-as-judge, hallucination surveys, LLM multi-agent surveys, SelfCheckGPT, TruthfulQA, AgentBench, WebArena, GAIA, LoCoMo, SWE-bench, and ALFWorld. Use this list only when the project topic matches those families; unrelated domains need their own literature-derived retrieval targets. Treat any starter list as retrieval targets only: fetch verified BibTeX from Semantic Scholar/arXiv/CrossRef/ACL/DBLP or official pages and add topic-specific papers until the final bibliography clears the 35-entry / 30-cited-key gate.
- Citation placement is part of paper quality: maintain a literature matrix by topic/claim/section, cite each paper near the claim or paragraph that discusses it, and avoid concentrating all references into one giant related-work paragraph, one mega-sentence, captions, or a detached bibliography dump.

## How to solve — 8 stages

```
research → plan → benchmark → run → analysis → draft → review → submission
```

### 1. research (brief + literature combined)
- Write `research/RESEARCH_BRIEF.md`: problem, gap, target venue
- Survey literature using all available sources:
  - arXiv (`arxiv-paper-search.md`): latest preprints and cutting-edge work
  - Semantic Scholar (`semantic-scholar-search.md`): published papers with citation counts
  - 机器之心/新智元 etc. (`research-brief-to-experiment-plan.md` §3): trend signals, hot topics, practitioner insights
- Write `research/LITERATURE_GROUNDING.json` (10+ recent papers, 3+ classic anchors, trend_sources)
- Write `research/LIT_MATRIX.tsv`, `research/SOURCE_DISCOVERY.md`, `research/TREND_INSIGHTS.md`
- Gate: real gap identified, relevant baselines found, benchmark options exist, trend sources checked
- Reviewer LLM check: none (code checks only)

### 2. plan (experiment design)
- Write `research/EXPERIMENT_PLAN.md`: method, baselines (≥3 non-trivial), metrics, benchmarks (≥3 sources)
- Choose training/inference framework per `training-infrastructure-guide.md`
- Gate: plan passes experiment-plan-review (LLM check)
- Reviewer LLM check: ⚡ experiment-plan-review.md (method competitiveness, baseline strength, eval fairness, infra)

### 3. benchmark (data preparation)
- Construct/select benchmarks, write `experiments/BENCHMARK_PROVENANCE.md`
- ≥240 tasks per condition, ≥3 independent benchmark families
- No synthetic-only final evidence
- Gate: datasets exist, gold answers verified

### 4. run (execute experiments)
- **Smoke-first strategy**: run a small pilot (10-50 tasks) to verify the idea works BEFORE full-scale
- If smoke shows the idea is invalid → pivot immediately, don't waste GPU hours
- Once smoke validates → submit full experiment via subagent (supervised mode)
- **Do NOT block on full training**. After submitting, advance to analysis/draft:
  1. Submit full run: `python -m argus_skill.tools.subagent submit --task-id train-full --mode supervised --run-dir experiments/train-full --command '...'`
  2. While waiting: start drafting paper structure, write method section, prepare figure templates
  3. When subagent reports completion → fill in actual results
- Use approved frameworks (vLLM, LLaMA-Factory, etc.)
- Write status.json, progress.jsonl, raw results
- Gate: smoke results validate idea direction; full results pass experiment-results-review
- Reviewer LLM check: ⚡ experiment-results-review.md (significance, ablation fairness, effect size)

### 5. analysis (results → claims + figures)
- Can start partially while full experiments still running (prepare templates, write analysis framework)
- Generate results_table.tsv, significance.tsv, figures once results arrive
- Write RESULTS_REPORT.md with supported/rejected claims
- Build claim-evidence mapping
- Gate: figures exist, claims backed by numbers

### 6. draft (write paper)
- Can start paper skeleton while experiments run — write intro, method, related work
- Fill in results/analysis sections when experiment data arrives from subagent
- Write paper/main.tex following EMNLP format (8 pages body)
- Generate Figure 1 via paper-framework-figure-studio-pro S0-S7
- Compile PDF
- Gate: PDF compiles, all sections present, story coherent
- Reviewer LLM check: ⚡ **lenient** academic-paper-peer-review-benchmark (score 3+ = pass). Checks structure completeness, not polish. Language/formatting fixed in review stage.

### 7. review (final reviews)
- Run academic language review, layout review, infrastructure review
- These are L2 heavy reviews (LLM API calls), run by reviewer agent
- Fix issues found, recompile, rerun only failed reviews
- Gate: all reviews score ≥ threshold

### 8. submission (final gate)
- Write SUBMISSION_ASSURANCE.json
- Confirm the full EMNLP submission contract across every stage checklist has no unresolved L2 reviewer issues
- Reviewer LLM check: ⚡ **strict** academic-paper-peer-review-benchmark (score 5+ = pass). Evaluates as actual EMNLP reviewer.
- Gate: structural gate clean AND LLM peer review passes → project complete, daemon auto-stops


## Review artifact integrity
- Review artifacts are generated evidence, not knobs. Do not hand-edit, normalize, or append PASS records for review JSON files; a top-level PASS is invalid when nested `model_review` or `vision_review` still reports revise, major/blocking issues, failed checks, low scores, leaks, or revision directives. Repair the manuscript/layout and run the owning reviewer with `--write`.


## Gate semantics
- `go`: advance to the next stage and update `research/PIPELINE_STATE.json`.
- `blocked`: pause because an external requirement is missing, such as credentials, data access, or LaTeX.
- `pivot`: preserve the artifacts and write the next research direction.
- `rejected`: stop and write why the idea should not become a paper.
- `done`: only for a stage whose required artifacts exist and are internally consistent.

## Response shape
- Report the current stage, gate verdict, and changed artifacts.
- If blocked/pivot/rejected, state the minimum condition needed to resume.
- Never summarize the project as submission-ready without pointing to `paper/SUBMISSION_ASSURANCE.json`.
