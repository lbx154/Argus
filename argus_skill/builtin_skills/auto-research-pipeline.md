---
name: Auto Research Pipeline
description: Orchestrate an end-to-end auto-research mission as a gated state machine from research brief through experiments, narrative report, paper draft, assurance, revision, and submission package.
category: research-orchestration
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Auto Research Pipeline

## Description
Run a complete research project as a gated artifact workflow rather than a one-shot paper generator. This is the argus-skill-native orchestrator inspired by ARIS research-pipeline: it coordinates planning, benchmark selection, experiments, result-to-claim analysis, paper writing, and submission assurance while preserving explicit pivot/reject paths.

## Non-negotiable project bar
- The project must target a real frontier gap in the selected domain, grounded in current papers, official benchmark reports/leaderboards, and strong baselines. A local mechanism demo, synthetic proxy, or validator-shaped paper is not enough.
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

## Pipeline state contract
Create or update `research/PIPELINE_STATE.json` before doing expensive work. The file is the mission ledger, not decorative prose:

```json
{
  "current_stage": "plan",
  "objective": "short operator objective",
  "target_venue": "EMNLP",
  "stages": {
    "brief": {"status": "done", "artifact": "research/RESEARCH_BRIEF.md"},
    "literature": {"status": "pending", "artifact": "research/LITERATURE_GROUNDING.json"},
    "novelty": {"status": "pending", "artifact": "research/IDEA_PROVENANCE.json"},
    "plan": {"status": "running", "artifact": "research/EXPERIMENT_PLAN.md"},
    "benchmark": {"status": "missing"},
    "run": {"status": "missing"},
    "analysis": {"status": "missing"},
    "narrative": {"status": "missing"},
    "draft": {"status": "missing"},
    "assurance": {"status": "missing"},
    "revision": {"status": "missing"},
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
- Every generated artifact, including `paper/RESULTS_REPORT.md`, `research/NARRATIVE_REPORT.md`, `paper/main.tex`, `paper/submission/main.tex`, reports, and figures, must list `sources` that transitively reach a canonical source such as raw results or canonical TSV/JSON summaries. Do not hand-write missing `sources` unless the refresh tool cannot infer them and `validate-manifest` names the exact remaining source gap.
- Do not hand-edit numbers in generated prose or LaTeX. Update the canonical source, regenerate downstream artifacts, then run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .`.
- After regenerated artifacts are stable, run `python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .` so `paper/ARTIFACT_FRESHNESS.json` records current generated outputs and their input hashes.
- If `VALIDATION_PRIORITY_POLICY.json` is missing or reports route errors, run `python -m argus_skill.skills.pipeline_contracts write-validation-priority-policy --project-root .`; do not hand-write a partial policy.
- If final validation reports manifest, freshness, and validation-route drift together, run `python -m argus_skill.skills.pipeline_contracts repair-emnlp-contract-artifacts --project-root .` after regenerating content artifacts; then inspect any remaining issues as real content/evidence blockers.
- Before marking `analysis`, `narrative`, `draft`, `assurance`, or `submission` as `ready`/`done`, run `python -m argus_skill.skills.pipeline_contracts validate-pipeline --project-root .`. If it reports digest drift, TSV schema drift, unknown sources, or missing manifest entries, the stage is `blocked` until regenerated. Before any final EMNLP-ready claim, also run `python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .`; a passing stage-sensitive pipeline check alone is not sufficient.

## Final EMNLP completion contract
- Treat intermediate work as `bounded` scope: literature, pilot feasibility, benchmark scale-up, baseline/ablation implementation, drafting, assurance, and revision can complete on their own acceptance criteria without proving the whole paper is ready.
- Use `final_submission` scope only for the project-final task that claims the submission package is complete.
- A full EMNLP submission experiment is not complete until `python -m argus_skill.skills.pipeline_contracts validate-full-emnlp --project-root .` exits 0 and the output is quoted in the completion evidence.
- The full-scale experiment sub-gate is explicit: `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .` must pass before `analysis`, `narrative`, `draft`, `assurance`, or `submission` can be marked `ready`/`done`.
- Benchmark construction is not execution. `benchmarks/full/tasks.jsonl` or a full benchmark manifest proves only that tasks were assembled; it does not satisfy run evidence until `experiments/**/results.jsonl`, `progress.jsonl`, or equivalent raw rows show completed scored trials.
- For required method/baseline matrices, every required condition must have at least 240 distinct scored main tasks/episodes. A completed `status.json` or declared `task_count: 240` is insufficient if raw result rows cover fewer tasks or omit a baseline. Treat `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, and `pilot_pdf_without_full_scale_evidence` as hard blockers.
- `validate-pipeline`, `validate-manifest`, a PDF existing on disk, or a small pilot is never sufficient final evidence.
- The minimum final evidence package must include enough benchmark coverage for the paper contract: at least 240 unique semantic scored main benchmark tasks/episodes (targeting the 240/250-task scale used by the worked examples), nontrivial baselines, ablations/failure analysis, paper-quality calibration, submission assurance, and a long-paper draft that satisfies the official ACL/EMNLP page/template contract. A 50/60-task run is only pilot evidence, not final EMNLP evidence.
- Hard prohibition: the final benchmark cannot be a duplicated pilot. Do not copy, relabel, suffix (`_r2`, `_copy`, `_dup`, etc.), shuffle, or otherwise rename the same episodes/prompts/specs/gold answers to inflate task count; this is an experiment-integrity blocker even if tables say `n_tasks >= 240`.
- Benchmark selection must be literature-grounded: the plan/provenance must show a survey of recent/frontier, widely used public benchmarks and official repos (for example ToolBench/ToolEval, WebArena/MiniWoB++/Mind2Web, GAIA, AgentBench/ALFWorld, MultiAgentBench, SWE-bench, LoCoMo, or a domain-specific ACL benchmark) before relying on synthetic tasks.
- Final benchmark evidence must not use synthetic tasks at all. Synthetic/local tasks can only be smoke tests and must not appear in main result tables or paper claims.
- Final benchmark evidence must not be single-source or planned-only. The plan/provenance must list at least 3 independent executed existing real benchmark sources/components as a hard minimum. For each selected source, record URL/repo, paper/citation/DOI, version/date, split/filtering, unique task count, license/access, capability/failure mode covered, execution status, and rationale for why this source belongs in the evidence mix. Planned diagnostic rows do not count until raw scored rows exist.
- Final method evidence must include a model-scale plan and training/adaptation artifacts when the contribution is a learned method: model/backbone, parameter count, trainable parameter count, training data, GPU memory plan, wall/GPU-hours, checkpoints, and exact evaluation command. A compact heuristic scorer or exact lookahead policy can be reported only as a baseline/ablation unless operator-approved as a non-frontier scope.
- The final paper must satisfy the `research.md` formatting contract, not merely compile: official ACL/EMNLP review template, anonymous author block (`Anonymous EMNLP Submission`), body visibly filled to the long-paper budget (Conclusion not before page 7 and References/Appendix not before page 9 when PDF text can be extracted), conclusion by the end of page 8, Limitations and Ethical Considerations present after the conclusion, References before Appendix, no total-page maximum after References/Appendix begin, and a complete reproducibility appendix.
- PDF preflight is a hard gate: no undefined references, no citation warnings, no `[?]` in rendered text, no `Overfull \hbox > 5pt`, no `\textbf{[PLACEHOLDER]}` strings, no `% UNVERIFIED` entries in `refs.bib` unless the operator explicitly accepts unresolved verification, at least 35 verified BibTeX entries, at least 30 unique cited keys, at least two rendered References pages when PDF text extraction is available, no rendered `and 1 others`/`and N others`, no BibTeX `author={... and others}` placeholders, no citation dumping, and no code-font/snake_case display labels in abstracts, headings, captions, figures, or tables unless explicitly justified.
- Figure/table preflight is a hard gate: every figure has a `\label{}` and is referenced in text, every table has a caption with a numerical headline, at least one figure or table appears on each of pages 4--7, and at least one paired-significance table exists when comparative binary outcomes are reported.
- Body layout must stay reviewable: <=5 body figures total, only one `figure*` full-width float, `[t]` used only for critical figures, no overlapping or interleaved floats, no unreadable tiny tables, and no table/text overflow above the 5pt overfull threshold.
- Figure 1, teaser, overall, and core method/framework/system/pipeline overview figures must use the actual image-2/codex-image2 raster output in `paper/main.tex`. **Hard prohibition:** the engineer must not draw, redraw, trace, clean, or "improve" these conceptual overview figures with matplotlib/FancyBboxPatch, TikZ node graphs, Inkscape/manual vector tools, screenshots, PIL/SVG/HTML canvases, or cleaned PDF derivatives. If the overview is ugly, write a better image-2 prompt, generate/select/review new image-2 attempts, and replace the raster `output_path`; do not self-draw a replacement or label a local raster as `codex-image2`. Record width/height, SHA-256 hashes, generation provenance, raw image-tool `sidecar_path`, `inspect_path`, and model-backed `review_path`; pass a 4/5-or-better image review from the `image_review` route, and avoid square `1024x1024` output. Prefer adaptive or landscape academic diagrams sized for page-width reading.
- The overview/teaser prompt must use the imported `research.md` six-section image-2 scaffold: `General style`, `Style intent`, `Pinned content`, `Layout variant`, `Negative prompt / Avoid`, and Figma tokens. Generate 6--20 variants by swapping only the named layout variant from the canonical 20-option menu; do not improvise a one-line prompt.
- Tables must follow the `research.md` styling tokens before final readiness: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent for meaningful degradation only, and bold winning values.
- Final submission must include a clean dedicated format preflight: invoke the EMNLP Format Preflight skill and run `python -m argus_skill.skills.pipeline_contracts validate-research-md-format --project-root .` before academic-language or layout review.
- Final submission must include a tool-generated academic-language review: `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write` followed by `python -m argus_skill.skills.pipeline_contracts validate-academic-language-review --project-root .`. `paper/ACADEMIC_LANGUAGE_REVIEW.json` must score at least 4/5, use a model-backed reviewer with quoted evidence spans and fresh LaTeX source hashes, set `needs_revision: false`, and contain no active revision directives.
- Final submission must include a tool-generated visual layout review: `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write` followed by `python -m argus_skill.skills.pipeline_contracts validate-layout-review --project-root .`. `paper/LAYOUT_REVIEW.json` must score at least 4/5, use rendered PDF page snapshots with fresh hashes, set `needs_revision: false`, and contain no blocking issues.
- Review artifacts are generated evidence, not knobs. Do not hand-edit, normalize, or append PASS records for `paper/ACADEMIC_LANGUAGE_REVIEW.*` or `paper/LAYOUT_REVIEW.*`; a top-level PASS is invalid when nested `model_review` or `vision_review` still reports revise, major/blocking issues, failed checks, low scores, or revision directives. Repair the manuscript/layout and rerun the owning review command.
- Final submission must include thick exemplar learning: invoke the Paper Exemplar PDF Learning skill, download at least two open-access top-conference exemplar PDFs under `paper/style_ref/exemplars/`, extract text, record `pdf_sha256`, write a thick `paper/style_ref/STYLE_PROFILE.md`, write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`, and pass `python -m argus_skill.skills.pipeline_contracts validate-exemplar --project-root .`. At least one exemplar should be a recent EMNLP/ACL best/outstanding/award paper when available. URL-only `EXEMPLAR.json` entries are blockers. After drafting, `paper/style_ref/STRUCTURE_CONFORMANCE.md` and `paper/style_ref/STRUCTURE_CONFORMANCE.json` must map every final top-level section to exemplar phases, evidence sources, exemplar lessons, and justified deviations; final readiness fails on unmapped filler sections.
- For positive paper objectives, final readiness requires `paper/PAPER_QUALITY_CALIBRATION.json.paper_contribution` with a one-sentence X-Y-Z-W claim: "We propose X. We show X improves Y by Z because W." The proposed artifact/protocol must beat the strongest nontrivial baseline on the declared primary metric, including held-out/public-validation splits when present, and the claim must cite statistical-support artifacts.
- Do not convert a failed proposed method into a negative-result paper to satisfy the gate. If smoke or main experiments reject the method-positive thesis, queue repair/pivot tasks for the method, metric, benchmark, or objective; do not mark `final_submission` done unless the operator explicitly asked for a negative-result paper.
- Standard starter citation targets for memory / agent-skills / hallucination projects include ReAct, Reflexion, SELF-REFINE, Toolformer, ToolLLM, API-Bank, Gorilla, HuggingGPT, MRKL Systems, Voyager, ExpeL, MemGPT, Generative Agents, A-Mem, MemoryBank, LongMem, WebRL, WebEvolver, Mobile-Agent-E, SAGE, SkillRL, Let's Verify Step by Step, STaR, MT-Bench/LLM-as-judge, hallucination surveys, LLM multi-agent surveys, SelfCheckGPT, TruthfulQA, AgentBench, WebArena, GAIA, LoCoMo, SWE-bench, and ALFWorld. Use this list only when the project topic matches those families; unrelated domains need their own literature-derived retrieval targets. Treat any starter list as retrieval targets only: fetch verified BibTeX from Semantic Scholar/arXiv/CrossRef/ACL/DBLP or official pages and add topic-specific papers until the final bibliography clears the 35-entry / 30-cited-key gate.
- Citation placement is part of paper quality: maintain a literature matrix by topic/claim/section, cite each paper near the claim or paragraph that discusses it, and avoid concentrating all references into one giant related-work paragraph, one mega-sentence, captions, or a detached bibliography dump.

## How to solve
1. **Seed brief and scope gate**
   - Create or read `research/RESEARCH_BRIEF.md`.
   - Treat the brief as an operator seed and constraint envelope, not the research idea. Record target venue, broad topic/task family, allowed resources, compute/API budget, risk tolerance, and what would make the mission not worth pursuing.
   - Do not invent the final thesis, method, benchmark, or falsifiable claim in the brief. Those must be derived after literature/news/code discovery.
   - If the objective is too broad, write `research/GO_NO_GO.md` with `blocked` and the missing decision instead of inventing a narrower target.

2. **Literature grounding and source-discovery gate**
   - Before choosing the final thesis or experiment plan, survey the target field.
   - Prefer 10 recent high-quality papers from the current system year when enough credible papers exist; if the current-year set is too sparse or mostly unreviewed preprints, explicitly fill the matrix with strong papers from the previous two years and explain the gap.
   - Add at least 3 classic anchor papers that define the task, benchmark, evaluation protocol, or core method family. Do not let a trend-only idea advance without classic grounding.
   - Use scholarly sources first: ACL Anthology, arXiv, Semantic Scholar, OpenReview, Papers with Code, official conference award/program pages, and dataset/benchmark papers.
   - Also scan operator-specified trend sources such as 机器之心 and 新智元 posts for emerging topics, industry framing, product signals, pain points, and pointers to papers or code. Treat these as non-peer-reviewed discovery signals: they do not need paper/benchmark/code backing to be recorded, but they also cannot by themselves support paper claims.
   - First write source access status in `research/SOURCE_DISCOVERY.md`: direct URL tried, HTTP result, date, accessible fallback, and whether the source is usable. For 新智元, try the currently accessible official site (`aiera.com.cn`) before marking the source blocked; for 机器之心, record whether article/search pages or only the data-service page are reachable.
   - Write `research/TREND_INSIGHTS.md` as a decision artifact, not a news digest. Extract repeated pain points, emerging evaluation settings, datasets/tools people care about, surprising practitioner constraints, unresolved questions, and hype claims that require verification.
   - Convert each useful trend into a testable research question with possible benchmark, baseline implication, cost/risk estimate, and decision: `use`, `watch`, `reject`, or `needs-scholarly-grounding-for-claim`.
   - Do not spend model/API budget on benchmark runs until at least one candidate research question is backed by both the literature matrix and a usable trend insight or an explicit reason why the trend scan is unavailable.
   - Never copy paper or media prose into artifacts. Store metadata, URLs, short paraphrased summaries, and your own analysis.
   - Write `research/LITERATURE_REVIEW.md`, `research/LIT_MATRIX.tsv`, `research/LITERATURE_GROUNDING.json`, `research/SOURCE_DISCOVERY.md`, and `research/TREND_INSIGHTS.md`. The JSON must contain `recent_high_quality_papers` (minimum 10), `classic_papers` (minimum 3), and `trend_sources` with source name, URL, access date, and extracted signals. Trend sources do not need paper/benchmark/code backing, but any trend-derived final claim must later be supported by paper/code/benchmark evidence or local experiment artifacts. The matrix must include source type, date, venue/status, URL, task, method, dataset, baseline, metric, key result, limitation, and why it matters for this project.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-grounding --project-root .` before marking the literature, novelty, or plan stage ready.
   - Gate outcome:
     - `go`: the field map identifies a real gap, relevant baselines, and benchmark options.
     - `blocked`: scholarly or specified-source access is unavailable; record the exact source and retry path.
     - `pivot`: the trend scan suggests a better nearby research question.
     - `rejected`: the literature already solves the proposed idea or leaves no testable contribution.

3. **Novelty and benchmark gate**
   - Generate candidate ideas only from the literature matrix, classic anchors, trend signals, benchmark gaps, and official paper/code repositories. Do not use free-form agent brainstorming as a source of ideas.
   - Write `research/IDEA_PROVENANCE.json` with `idea_generation_mode: literature_and_code_grounded`, `not_agent_brainstorm: true`, at least 3 literature-derived `candidate_ideas`, and a `selected_idea` whose `derived_from` sources point to papers/benchmarks/code.
   - Search local artifacts and available public sources for relevant existing benchmark options and official implementation sources. Do not generate synthetic tasks as a replacement for benchmark selection.
   - Build a selected benchmark source list before experiments: require at least 3 independent real/frontier benchmarks or components for final evidence, and explain how each source tests a different capability, domain, or failure mode. Planned diagnostics are allowed in the plan but do not count toward final evidence until executed.
   - Build a model-scale plan before implementation. Prefer adapting a modern domain backbone with the available GPU budget; reject plans whose proposed model is only a tiny scorer or prompt wrapper when the field expects learned models.
   - Write `research/CODE_REUSE_PLAN.json` with paper-code/repository search queries, surveyed URLs, source type, license/terms, attribution, and reuse decision (`use`, `adapt`, `fork`, `reference`, `baseline`, or `reject`). Prefer official paper code, benchmark repos, and well-licensed libraries over writing everything from scratch; if all code is rejected, justify why.
   - Write `research/NOVELTY_REPORT.md`, `research/NOVELTY_MAP.md`, `research/RELATED_WORK_BLOCKERS.md`, and `experiments/BENCHMARK_PROVENANCE.md`.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-idea-provenance --project-root .` and `validate-code-reuse --project-root .` before marking novelty or plan ready.
   - Gate outcome:
     - `go`: credible existing real benchmark sources and a meaningful method/model-scale plan exist.
     - `pivot`: the idea is interesting but the benchmark does not test it.
     - `blocked`: benchmark access, model weights, data license, or compute prevents a faithful real-benchmark trained-method experiment.
     - `rejected`: the idea is already solved, untestable, or unsupported by available evidence.

4. **Experiment plan gate**
   - Invoke the Research Brief To Experiment Plan skill.
   - Require `research/EXPERIMENT_PLAN.md`, `research/CLAIMS_TO_TEST.md`, `research/BASELINE_AND_BENCHMARK_PLAN.md`, `research/IDEA_PROVENANCE.json`, and `research/CODE_REUSE_PLAN.json`.
   - Every candidate paper claim must map to at least one planned run and one expected raw artifact. Every baseline chosen from literature/media discovery must be labeled as `required`, `optional`, or `blocked` with a reason.
   - Implementation tasks should start from the code reuse plan: prefer license-compatible official paper code or benchmark repos when appropriate, cite/attribute them, and only write from scratch after a recorded survey and justification.

5. **Benchmark execution gate**
   - Invoke the Agent Research Benchmark Runner skill.
   - Require a run directory with `manifest.json`, `status.json`, `progress.jsonl`, logs, raw result rows, and a documented `STOP` cancellation contract.
   - Do not mark the full benchmark run done unless raw experiment rows, not only status/manifest summaries, prove at least 240 unique semantic scored main tasks/episodes for every required method/baseline condition. If the current run has only 50--60 tasks or only one condition, queue a real scale-up/matrix-completion run instead of moving to analysis or drafting. The 240+ tasks must come from documented existing real/frontier benchmark sources; they must not be synthetic, generated, duplicate, or relabelled copies.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-full-scale-evidence --project-root .` before advancing to analysis. Any `missing_full_scale_experiment_run`, `incomplete_full_scale_experiment_run`, `missing_baseline_condition_run`, or `pilot_pdf_without_full_scale_evidence` issue keeps downstream stages blocked.
   - Require benchmark provenance to include surveyed frontier/public benchmarks, a selected benchmark source table/list, and the rationale for the selected real-benchmark mix. If the only feasible evidence would be synthetic tasks, mark the project blocked or pivot.
   - Require model training/adaptation artifacts for learned contributions: checkpoint or adapter path, training logs, config, dataset split, model card, and evaluation command. A tiny scorer-only run must route to baseline/ablation, not final proposed-method evidence.
   - Do not advance if model IDs, task pairing, metrics, or budget no longer match the plan.

6. **Analysis and result-to-claim gate**
   - Invoke the Research Results Analysis And Figures skill.
   - Require `paper/RESULTS_REPORT.md`, `paper/artifacts/claims_evidence.tsv`, and, when comparisons exist, a result-to-claim table such as `paper/artifacts/result_to_claim.tsv`.
   - If `validate-full-scale-evidence` fails, analysis artifacts may be written only as pilot diagnostics; do not write final result claims, full-paper narrative, or downstream-ready pipeline state from smoke-only evidence or from a benchmark split that has not executed.
   - Claims may become `supported`, `weak`, `rejected`, or `missing`; weak evidence must stay labeled weak downstream.
   - Before drafting, run the paper-quality calibration checks when available (`argus_skill.skills.paper_calibration`). If the analysis matches the negative fresh-demo pilot pattern--for example baseline not beaten, synthetic-only benchmark, fewer than 240 scored main tasks/episodes, parser/schema confound, or draft self-reporting as not submission quality--mark the next stage `pivot` or `rejected` instead of writing LaTeX.

7. **Narrative handoff gate**
   - Write `research/NARRATIVE_REPORT.md` before any LaTeX drafting.
   - Include the problem framing, method/protocol, benchmark provenance, exact supported claims, rejected claims, failure taxonomy, figure/table inventory, limitations, and missing evidence.
   - Use the inner/outer synthesis loop from the default research workflow: first verify each experiment claim against raw evidence, then synthesize the global story and update the thesis sentence. Do not let LaTeX drafting become the first synthesis step.
   - If the narrative is pilot-scale, mark the target as `pilot-note` or `short/workshop` instead of padding to a long paper.

8. **Paper drafting gate**
   - Invoke the EMNLP Paper Drafting skill only after the narrative report exists.
   - Require official ACL/EMNLP template metadata, page budget, thick style profile, `paper/style_ref/EXEMPLAR.json`, `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.md`, `paper/style_ref/STRUCTURE_CONFORMANCE.json`, local exemplar PDFs/text extracts, `paper/PAPER_DRAFT_REPORT.json`, and evidence comments for every numeric claim.
   - Every project must invoke Paper Exemplar PDF Learning: download at least two excellent open-access EMNLP/ACL paper PDFs, extract text, compute `pdf_sha256`, write `paper/style_ref/STYLE_PROFILE.md`, then write `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` as the concrete paper organizer before prose. After drafting, write `STRUCTURE_CONFORMANCE` from the actual LaTeX section order so every section maps to an exemplar phase, local evidence source, and applied lesson. Use the exemplars only for structural learning: section allocation, paragraph roles, figure density, related-work shape, evaluation layout, formatting, and prose discipline. Never copy prose, examples, claims, terminology, bibliography text, or figures.
   - Require a full EMNLP long-paper target: `target_venue: EMNLP`, `paper_scope: long-paper`, 7.5--8 main-content pages, References/Appendix starting on page 9 or later with no total-page maximum after that boundary, official ACL template, and at least 240 scored main benchmark tasks/episodes. `paper/PAGE_BUDGET.md` and `paper/style_ref/PAPER_STRUCTURE_BLUEPRINT.md` should start from this reference allocation unless evidence/exemplars justify a change: Abstract 0.3 pages; Introduction 1; Related Work 0.5--0.8; Method 1--1.5; Experimental Setup 0.5--1; Main Results 1--1.5; Analysis/Ablation 1; Failure Cases 0.3--0.5; Conclusion 0.2. The drafting gate passes only when `submission_quality_self_assessment: ready` is legitimate; otherwise the draft report must say `pilot`, `not_ready`, or `blocked`, and the pipeline stage must stay non-`done`.
   - If the draft cannot fill the body budget without looking strange, route the project backward instead of padding: `missing_full_scale_experiment_run`/`missing_baseline_condition_run` -> run more benchmark conditions; weak or missing ablations -> run ablation/sensitivity experiments; missing story depth -> generate failure taxonomy, robustness/public-validation analysis, error slices, or claim downgrades from raw logs. Update `paper/EVIDENCE_GAPS.json`, `paper/CLAIM_GRAPH.json`, and `paper/VALIDATION_PRIORITY_POLICY.json` before drafting again.
   - For Figure 1, teaser, overall, and core conceptual/method/framework/system/pipeline overview figures, require `paper/figures/IMAGE2_FIGURES.json` and include the actual image-2 / codex-image2 raster `output_path` in `paper/main.tex`. The manifest must include `sidecar_path`, `inspect_path`, `review_path`, `generation_provenance_path`, prompt/output SHA-256, width/height, and API/tool evidence from the Argus image tool or `/images/generations`; manual-only visual review is not acceptable. The prompt must use the imported `research.md` scaffold with exact visible labels, negative constraints, and a named layout variant. Data plots and secondary precise TikZ/pgfplots diagrams may remain script/vector generated, but they must not replace the core overview image-2 figure. Do not let the engineer self-draw this overview with plotting/layout/vector code; prompt image-2 again instead.
   - Before marking draft ready, compile the PDF, save `paper/main.log`, invoke the EMNLP Format Preflight skill, run `validate-paper-format` and `validate-research-md-format`, and repair any `Overfull \hbox > 5pt`, table/body overlap, appendix-before-references ordering, unresolved references/citations, `[?]`, placeholders, `% UNVERIFIED` bibliography entries, insufficient bibliography depth, missing Limitations/Ethical Considerations, early References, underfilled body, missing visual pages, or ugly code-like display labels. Underfilled-body failures must be treated as `content_sufficiency` until evidence completeness is proven.
   - Repair citation hygiene as prose, not only as BibTeX count: related-work references should be grouped by method family, benchmark gap, or failure mode, with citations adjacent to the paper-specific claim they support. Use ACL/EMNLP author-year natbib style, not numeric citation overrides. Replace missing or abbreviated BibTeX authors (`and others`, `et al.`) with verified full metadata, ensure starter keys match the actual fetched title/source, brace acronyms in titles, and split any citation command above eight keys into topic-specific sentences.
   - Enforce the body layout contract: conclusion by page 8, References/Appendix on page 9 or later with no total-page maximum after that boundary, at least one figure/table on each of pages 4--7, <=5 body figures, only one `figure*`, every figure labeled/referenced, every table caption a numerical headline, at least one paired-significance table when comparative binary outcomes are reported, and complete reproducibility appendix.
   - Do not accept square `1024x1024` conceptual figures. Regenerate or redesign them as adaptive/landscape, preferably `1536x1024 or 1920x1080`, clean academic diagrams with readable labels and a review sidecar.
   - Run `python -m argus_skill.skills.academic_language_review --project-root . --review-mode model --write` after content stabilizes. If the score is below 4/5 or the review returns revision directives, rewrite the abstract/introduction/related work/claims and rerun before final layout work. Do not game this gate by putting evidence-span quotes, artifact paths, appendix/figure references, validation language, or defensive caveat lists into the abstract; those belong in review artifacts, captions, limitations, or result-to-claim mappings.
   - Run `python -m argus_skill.skills.paper_layout_review --project-root . --review-mode vision --write` after the final compile. If the score is below 4/5 or the review returns revision directives, revise layout/content/figures/tables, recompile, and rerun the review before entering assurance.
   - Never repair stale or failing review gates by editing review JSON/markdown/history files directly. The only accepted review refresh path is: stabilize the source, run the owning reviewer with `--write`, then run the matching validator.

9. **Submission assurance gate**
   - Invoke the Research Submission Assurance Gate skill.
   - Require `paper/SUBMISSION_ASSURANCE.md`, `paper/SUBMISSION_ASSURANCE.json`, `paper/PAPER_QUALITY_CALIBRATION.json`, `paper/FORMAT_PREFLIGHT.md`, `paper/ACADEMIC_LANGUAGE_REVIEW.json`, and `paper/LAYOUT_REVIEW.json`.
   - Require `validate-grounding`, `validate-idea-provenance`, `validate-code-reuse`, `validate-exemplar`, `validate-image2-figures`, `validate-paper-format`, `validate-research-md-format`, `validate-academic-language-review`, `validate-layout-review`, `validate-paper-contract`, `validate-manifest`, and `validate-full-emnlp` to pass before any `PASS` or submission-ready wording.
   - Use positive examples only as quality-signal sources. For example, EMNLP 2025 award metadata from the official awards page can calibrate expectations around public/resource-scale validation, strong baselines, and claim scope; it must not be used to copy prose.
   - Do not declare final EMNLP-ready unless the assurance verdict is `PASS`. An operator-accepted `WARN` can describe an explicitly non-final or non-submission scope, but it must not be called EMNLP-ready and cannot override any hard blocker.

10. **Revision or stop**
   - If assurance returns `FAIL`, `BLOCKED`, or `ERROR`, invoke the Paper Review Revision Loop skill with the assurance report as the required input.
   - If `paper/ACADEMIC_LANGUAGE_REVIEW.json` fails, use its `revision_directives` before cosmetic layout work: rewrite generic openings, tighten the X-Y-Z-W contribution sentence, calibrate claims, reorganize related work, and add evidence-backed captions/scope limits. Preserve natural EMNLP abstract prose: problem first, then method/result/implication, with internal audit evidence kept out of the abstract.
   - If `paper/LAYOUT_REVIEW.json` fails, use its `revision_directives` as the highest-priority layout tasks. Limit repeated layout-only attempts to three non-improving rounds; then mark the pipeline `blocked` with the remaining directives instead of claiming readiness.
   - If the missing evidence is fundamental, mark the pipeline `pivot` or `rejected`; do not keep rewriting.

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
