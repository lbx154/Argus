---
name: Research Results Analysis And Figures
description: Turn raw experiment outputs into paper-ready tables, plots, failure taxonomies, and a claims-evidence matrix without inventing missing numbers.
category: research-analysis
version: 1
scientist_model: gpt-5.4
created_at: 2026-05-23T00:00:00+00:00
---

## Title
Research Results Analysis And Figures

## Description
Analyze completed runs and generate figure/table artifacts for a paper. This adapts ARIS paper-figure/analyze-results ideas to argus-skill: the agent must derive data figures and tables from local raw data, while conceptual overview figures must come from documented image-2 prompt/provenance artifacts rather than fabricated data or local redraws.

## When to use
- The objective asks for plots, tables, result analysis, failure taxonomy, or paper figures.
- Experiment artifacts exist under `experiments/`, `benchmarks/results/`, `benchmarks/evidence/`, or similar.
- The paper needs evidence-backed claims rather than new experiments.

## When NOT to use
- No raw results exist and the task is to run the experiments first.
- The operator asks for conceptual diagrams only; use the paper drafting skill and write figure specifications instead.
- The task asks for marketing graphics rather than scientific figures.

## How to solve
1. Inventory raw data:
   - Search for JSON, JSONL, TSV, CSV, logs, verifier outputs, and manifests.
   - Build a source table: artifact path, run id, metric fields, model/backend, timestamp, and data quality notes.

2. Validate before computing:
   - Check that compared rows use the same dataset/task split and compatible metrics.
   - Flag missing seeds, missing baselines, duplicate run ids, partial runs, and failed verifier outputs.
   - Keep negative and failed runs in the analysis unless the plan explicitly excludes them.

3. Create an analysis script:
   - Prefer `paper/analysis/build_results.py` or `analysis/build_results.py`.
   - Read raw artifacts, normalize schemas, compute aggregate tables, and write derived files.
   - Do not hard-code final numbers in prose; derive them from input files.
   - Declare output schemas in code before writing TSV/CSV files and filter rows to those schemas; a row with extra or missing fields is a generation error, not a warning.

4. Generate paper artifacts:
   - `paper/artifacts/results_table.tsv` for main metrics.
   - `paper/artifacts/failure_taxonomy.tsv` when failures or reviewer categories matter.
   - `paper/figures/*.pdf` or `.png` with readable labels, units, and captions.
   - `paper/figures/IMAGE2_FIGURES.json` for Figure 1, teaser, overall, and conceptual/method/framework/system overview figures. At least one core conceptual figure must be generated with image-2 / codex-image2, with a checked-in prompt path, generation provenance sidecar, SHA-256-tracked raster output path, and direct inclusion by `paper/main.tex`. **Do not draw the overview/teaser yourself** with matplotlib, FancyBboxPatch, TikZ, SVG/PIL/HTML canvas, Inkscape, cleaned PDFs, or screenshots, and never label a local raster as `codex-image2`. Do not crop, downsample, resave, or overwrite the generated raster after provenance is written; the actual image dimensions and SHA-256 must stay synchronized with the prompt/provenance/review sidecars. Data plots, pgfplots, matplotlib exports, and secondary TikZ diagrams remain allowed when generated from scripts or precise vector specs, but they must not replace the core overview image-2 figure.
   - `paper/artifacts/claims_evidence.tsv` mapping each claim to raw evidence paths.
   - `paper/artifacts/result_to_claim.tsv` mapping each planned claim to `supported`, `weak`, `rejected`, `missing`, or `contradicted` before drafting begins.
   - `paper/ARTIFACT_MANIFEST.json` mapping canonical sources to every generated report, table, figure, and downstream manuscript copy. Include SHA-256 digests and exact TSV `columns`.
   - Generate table LaTeX to match the `research.md` formatting contract: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent for meaningful degradation only, and bold winning values. If a table cannot fit under `No Overfull \hbox > 5pt`, split it or move low-value diagnostics to the appendix.
   - Every table caption must be a numerical headline, not a generic description; for comparative binary outcomes include a paired-significance table or explicitly mark significance as not applicable.
   - Generate figure assets with final-paper placement in mind: body figures <=5 total, only one `figure*` full-width candidate, `[t]` reserved for critical figures, and an intended figure/table inventory that gives pages 4--7 at least one visual each.
   - Conceptual/method/system figures must be adaptive/landscape page-width image-2 assets, preferably `1536x1024 or 1920x1080`, with clean Figma-style rounded cards, readable labels, exact spelling, and no square `1024x1024` outputs, tiny text, decorative gradients, photorealism, weird fonts, or snake_case/code labels. If they fail review, regenerate through image-2 using a better prompt; do not self-draw a substitute.
   - Every figure needs a stable filename, `\label{}` suggestion, text-reference suggestion, source data or prompt path, width/height, and review metadata so the drafting/layout skills can reject stale or ugly visuals.

5. Statistical discipline:
   - For multiple seeds, report mean and dispersion.
   - For single-run pilots or any run with fewer than 240 scored main tasks/episodes, label them as pilot evidence, not conclusive proof; this label does not make them acceptable final EMNLP evidence.
   - For final EMNLP analysis, the canonical results table must include an overall main split with `n_tasks >= 240` before claims can be treated as full-paper evidence.
   - Avoid significance language unless the appropriate test was actually computed.

6. Write `paper/RESULTS_REPORT.md`:
   - Summarize what the data supports, weakens, or rejects.
   - Include exact artifact paths and commands used to regenerate figures.
   - List missing evidence as TODOs rather than filling gaps with estimates.
   - Treat canonical tables/JSON as the only source of reported numbers; regenerate the report after any table change.
   - Include a `research.md format readiness` subsection listing table-style compliance, figure aspect ratios, paired-significance status, and whether any generated artifact is likely to cause `Overfull \hbox > 5pt`.

7. Write the narrative handoff:
   - Create or update `research/NARRATIVE_REPORT.md` with problem framing, benchmark provenance, method/protocol, supported claims, weakened/rejected claims, failure taxonomy, figure/table inventory, limitations, and the intended paper scope.
   - Read `research/LITERATURE_GROUNDING.json` and carry forward the recent-paper gap, classic anchors, trend signals, and required baselines into the narrative. Do not write the paper as if literature/news grounding happened when `validate-grounding` fails. Trend signals can motivate but cannot replace paper/code/benchmark or local-result evidence for technical claims.
   - Read `research/IDEA_PROVENANCE.json` and `research/CODE_REUSE_PLAN.json`; carry forward the paper-derived gap, selected-idea rationale, reused/adapted code sources, attribution, and any from-scratch justification.
   - If the intended claim cannot be positioned against both recent high-quality papers and classic anchors, mark the narrative `pivot` or `blocked` instead of inventing a contribution.
   - If the evidence is only pilot-scale, explicitly label the narrative `pilot-note` or `short/workshop` scale.
   - Update `research/PIPELINE_STATE.json` so the analysis and narrative stages are `done` only when their artifacts exist.

8. Verify outputs:
   - Run the analysis script from a clean shell.
   - Confirm every generated figure/table file exists and is non-empty.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-grounding --project-root .` before drafting so weak literature grounding cannot become a confident related-work section.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-idea-provenance --project-root .` and `validate-code-reuse --project-root .` before drafting so an agent-invented idea or untracked external code cannot become the paper's contribution.
   - Run `python -m argus_skill.skills.pipeline_contracts validate-image2-figures --project-root .` before drafting and again after LaTeX integration so conceptual figures cannot bypass image-2 or silently switch to a cleaned PDF/vector derivative.
   - Run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` after generation, then `python -m argus_skill.skills.pipeline_contracts validate-manifest --project-root .`.
   - If the manifest reports digest drift, TSV schema mismatch, generated-source cycles, or unknown sources, fix the generator and rerun it before updating `PIPELINE_STATE.json`.
   - Quote the command output in the final verification block.

## Response shape
- Provide a compact table of generated artifacts.
- Include the regeneration command and raw evidence paths.
- State any claims that must be softened because evidence is incomplete.
