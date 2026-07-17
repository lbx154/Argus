---
name: Research Results Analysis And Figures
description: Turn raw experiment outputs into paper-ready tables, plots, failure taxonomies, and a claims-evidence matrix without inventing missing numbers.
category: research-analysis
version: 1
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
   - For final empirical analysis, require completed raw evidence for every
     claim-relevant condition before generating paper-facing claims. Keep
     incomplete work labeled pilot/diagnostic and do not mark downstream stages
     ready.
   - Do not treat `benchmarks/full/tasks.jsonl`, `benchmarks/full/manifest.json`, or a declared `status.json task_count` as executed evidence. Final analysis needs completed raw scored rows per required method/baseline condition.
   - Keep negative and failed runs in the analysis unless the plan explicitly excludes them.
   - Every completed RL optimizer-step training run must carry its own training-curve plot under `<run_dir>/plots/` (reward/loss/grad-norm/KL/entropy/throughput vs optimizer step), sourced from that run's `progress.jsonl`/`verl_metrics.jsonl`. The `rl_training_plots` gate is **structural at the analysis stage**: an unplotted completed optimizer run blocks analysis. If any are missing, generate the per-run curve from the run's own logs (a script/vector data plot, this is allowed) before citing the run as evidence — see the rl-training-collapse-diagnosis skill for the contract.

3. Create an analysis script:
   - Prefer `paper/analysis/build_results.py` or `analysis/build_results.py`.
   - Read raw artifacts, normalize schemas, compute aggregate tables, and write derived files.
   - Do not hard-code final numbers in prose; derive them from input files.
   - Declare output schemas in code before writing TSV/CSV files and filter rows to those schemas; a row with extra or missing fields is a generation error, not a warning.
   - Apply the shared publication chart style to every data plot so figures look consistent and journal-grade instead of default-matplotlib ugly. Follow the **Paper Chart Styling** skill: `pip install matplotlib seaborn SciencePlots` in the project venv, copy `paper_chart_style.py` into `paper/analysis/`, and call `set_pub_style(venue=<venue>, column="single"|"double")` once at the top of `build_results.py` before creating any figure. Size each plot for the LaTeX float it will occupy (`figure_size("single")` for a one-column `figure`, `figure_size("double")` for a full-width `figure*`), use a colour-blind-safe palette with redundant encoding (colour + marker + linestyle), and `highlight_ours(...)` on the proposed method. Never use `jet`/rainbow colormaps. If the styling packages cannot be installed, fall back to plain matplotlib rather than blocking.

4. Generate paper artifacts:
   - `paper/artifacts/results_table.tsv` for main metrics.
   - `paper/artifacts/main_results_matrix.tsv` for the central public-evidence
     result table when the project is empirical. It should include source,
     split/cohort, evaluation unit, model/system, method/reference, metric,
     budget/configuration, result, and raw artifact fields appropriate to the
     domain. It must cover every public source and condition required by the
     final claims; no fixed source count is imposed.
   - `paper/artifacts/failure_taxonomy.tsv` when failures or reviewer categories matter.
   - `paper/figures/*.pdf` or `.png` with readable labels, units, and captions.
   - `paper/figures/IMAGE2_FIGURES.json` for every non-data figure: Figure 1, teaser, overall, conceptual/method/framework/system overview figures, schematics, qualitative/example visuals, and architecture/explanatory diagrams. At least one core conceptual figure must be generated with image-2 / codex-image2, with a checked-in prompt path, raw generation `sidecar_path`, `inspect_path`, model-backed `review_path`, generation provenance sidecar, SHA-256-tracked raster output path, and direct inclusion by `paper/main.tex`. The prompt must be created from the canonical Argus figure-studio scaffold via `python -m argus_skill.tools.image_tool paper-prompt ...` and must retain both markers: `argus-image2-paper-prompt-v1` and `paper-framework-figure-studio-pro-v3.1.4a`. The sidecar must come from the Argus image tool or record an `/images/generations` endpoint, model, created time, exact generation prompt text, `prompt_sha256`, accepted-raster SHA-256, and dimensions. Run inspect/review only after generation has returned and the output file exists; do not start missing-file review jobs in parallel with image generation. After review, run `python -m argus_skill.tools.image_tool sync-paper-metadata --project-root . --image <png> --prompt-file <prompt.txt> --figure-id <id> --figure-type <type>` so manifest/provenance hashes are synchronized from real files. A manual visual check or a hand-written `codex-image2` manifest is not proof. **Do not draw non-data figures yourself** with matplotlib, FancyBboxPatch, TikZ, SVG/PIL/HTML canvas, Inkscape, cleaned PDFs, or screenshots, and never label a local raster as `codex-image2`. Do not crop, downsample, resave, or overwrite the generated raster after provenance is written; the actual image dimensions, prompt text, and SHA-256 values must stay synchronized with the prompt/provenance/inspect/review sidecars. If prompt/provenance drift occurs, restore the original matching prompt or regenerate through image-2; never edit only metadata hashes to satisfy a validator. Data/metric/result plots remain allowed when generated from scripts or precise vector specs; every non-data figure must be image-2 generated.
   - `paper/artifacts/claims_evidence.tsv` mapping each claim to raw evidence paths.
   - `paper/artifacts/result_to_claim.tsv` mapping each planned claim to `supported`, `weak`, `rejected`, `missing`, or `contradicted` before drafting begins.
   - `paper/ARTIFACT_MANIFEST.json` mapping canonical sources to every generated report, table, figure, and downstream manuscript copy. Include SHA-256 digests and exact TSV `columns`.
   - Run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` after generated reports/tables/figures are refreshed, then `python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness --project-root .` after downstream paper/review artifacts are regenerated from those sources.
   - Generate table LaTeX to match the `research.md` formatting contract: `\footnotesize`, `\tabcolsep=3-4pt`, `\arraystretch=1.15`, light-gray header, soft peach "ours" row, alternating row tint for long tables, coral accent for meaningful degradation only, and bold winning values. If a table cannot fit under `No Overfull \hbox > 5pt`, split it or move low-value diagnostics to the appendix.
   - Generate a clear main results/evidence table appropriate to the paper. It
     should expose the selected public source(s), strongest relevant comparisons,
     and claim-critical outcomes without forcing a cross-benchmark matrix when
     the research design has a different natural evidence shape.
   - Every table caption must be a numerical headline, not a generic description; for comparative binary outcomes include a paired-significance table or explicitly mark significance as not applicable.
   - Generate figure assets with final-paper placement in mind: body figures <=5 total; the teaser and the main pipeline/architecture overview are the full-width `figure*` candidates (at most two), while sub-module/ablation/detail plots stay single-column `figure`; `[t]` reserved for critical figures, and an intended figure/table inventory that supports a readable middle-body rhythm under the vision layout reviewer. Do not add low-value visuals solely to hit page-number anchors.
   - Conceptual/method/system figures must be adaptive/landscape page-width image-2 assets, preferably `1536x1024 or 1920x1088` (dimensions divisible by 16), with clean Figma-style rounded cards, readable labels, exact spelling, and no square `1024x1024` outputs, tiny text, decorative gradients, photorealism, weird fonts, or snake_case/code labels. If they fail review, regenerate through image-2 using a better prompt; do not self-draw a substitute. If selecting one reviewed candidate as the final stable filename, keep a bit-identical raster copy whose SHA-256 matches the raw image-2 sidecar/provenance evidence rather than rewriting metadata or resaving the image.
   - Before Figure 1/teaser generation, freeze evidence and structure with
     `python -m argus_skill.tools.image_tool freeze-paper-context --project-root .`.
     Then query `paper-cache-status`. Generate 6--20 layout variants only for a
     new/changed freeze, review and sync every candidate, and reuse the passing
     cache once it reaches six. `paper/main.tex`, caption, citation, or minor
     layout edits are deliberately outside the freeze and must not trigger
     regeneration. Prompts still use the canonical figure-studio scaffold and
     markers.
   - The `paper-prompt` scaffold is Argus-adapted from `paper-framework-figure-studio-pro-v3.1.4a`: it uses paper foundation, candidate brief, separate image-2 raster candidates, figure-caption symbiosis, core-submodule visibility, and bounded final joint audit. Fill paper-specific labels but keep the style/negative/layout rules:
     - `General style`: selected-venue AI research figure, adaptive page-width
       landscape, `1536x1024` or `1920x1088`; clean block-based Figma
       style, rounded cards, neat alignment, soft pastel fills, dark-gray 2px
       borders, compact information density; little wasted space but not
       crowded; no heavy shadows, gradients, photorealism, glassmorphism, or
       messy Excalidraw look.
     - `Style intent`: clean, dense, modular, Figma-like, low-saturation pastel cards, sparse meaningful badges/icons, paper-main-figure quality rather than marketing/dashboard/stock/whiteboard.
     - `Pinned content`: quote every visible label exactly. Include a title, a `Show: source -> build/distill -> quality/verification gate -> memory/library/state -> agent/execution -> output -> benchmark/evidence` chain, and chips for baseline/status quo, proposed method, accepted item, rejected item, main metric/evidence, and failure avoided. Forbid raw paths, code identifiers, and invented terminology.
     - `Layout variant`: choose and name one variant ID; generate 6--20 variants by swapping only this block. Use variants such as 01 central hero, 02 horizontal swimlanes, 03 sankey funnel, 04 exploded entry, 05 layered architecture stack, 06 pipeline plus gallery, 07 modular dashboard, 08 radial hub-spoke, 09 zigzag pipeline, 10 research-poster dense, 11 grayscale accent, 12 color-coded phases, 13 card deck, 14 computation graph, 15 dataflow with sidebars, 16 timeline plus insets, 17 nested containers, 18 multi-panel A/B/C/D, 19 light blueprint, or 20 polished Figma wireframe.
     - `Negative prompt / Avoid`: no concrete code snippets, raw paths, tiny unreadable text, character-level vertical text, dense paragraphs, excessive logos, watermark, photorealistic scenes, stock photos, glassmorphism, heavy gradients/shadows, textures, arbitrary decorative blobs, messy whiteboard style, large empty areas, overlapping cards, squashed labels, inconsistent terminology, or dashboard-like extra captions.
     - `Figma tokens`: background `#fbfaf7`, stroke `#1f2933` at 2px, corner radius 10--16px, card padding 12--20px, card gap 12--24px, pastels `#ffe2d1`, `#fff2bd`, `#dcecff`, `#e2f7df`, `#eadfff`, `#fff1c9`, title 38--52px, section headers 22--30px, card labels 16--22px, chips 12--16px.
   - Every figure needs a stable filename, `\label{}` suggestion, text-reference suggestion, source data or prompt path, width/height, and review metadata so the drafting/layout skills can reject stale or ugly visuals.

5. Statistical discipline:
   - For multiple seeds, report mean and dispersion.
   - Label pilots as pilots. A focused single-public-source study may support a
     narrow final claim when the design, controls, and uncertainty justify it;
     broader claims require broader evidence.
   - For final empirical analysis, the canonical result artifacts must identify
     the public source, evaluation unit, model/system, comparison, and executed
     evidence for every claim-relevant condition.
   - The main results matrix should be the first thing a reviewer can inspect to answer: which benchmarks were run, which model/backend powered the evaluated system, which baselines were compared, how many tasks were scored, and what metric changed.
   - Avoid significance language unless the appropriate test was actually computed.

6. Write `paper/RESULTS_REPORT.md`:
   - Summarize what the data supports, weakens, or rejects.
   - When the method underperforms a baseline on a headline metric, present it honestly AND constructively: keep the losing comparison in the table, but also record where the method wins or ties (regime, sub-population), the mechanism or insight the loss reveals, any baseline confound or budget asymmetry, and the trade-off. Propose the scoped claim and the boundary analysis for the paper to use. Do NOT drop a planned claim-relevant comparison, cherry-pick the best metric, or spin a null; genuine nulls are reported as findings and routed to limitations/scope. Only technically-broken or inconclusive runs may be omitted, and only with a stated reason; peripheral exploratory runs that were never part of the core claim need not be reported.
   - Include exact artifact paths and regeneration commands only in provenance reports, manifests, or developer-facing run logs. Do not copy raw local paths, commands, GPU/device/cache settings, route names, private configuration, wall-clock log details, artifact hashes, status/progress log mechanics, or STOP-file contracts into rendered paper prose; captions and body text should use reader-facing artifact types, benchmark/model facts, and neutral replay-interface language.
   - List missing evidence as TODOs rather than filling gaps with estimates.
   - Treat canonical tables/JSON as the only source of reported numbers; regenerate the report after any table change.
   - Include a `research.md format readiness` subsection listing table-style compliance, figure aspect ratios, paired-significance status, and whether any generated artifact is likely to cause `Overfull \hbox > 5pt`.
   - Include a `content sufficiency` subsection: state whether the evidence can
     support the selected venue's paper format without filler. If not, write the
     exact missing evidence or claim revision to `paper/EVIDENCE_GAPS.json`.

7. Write the narrative handoff:
   - Create or update `research/NARRATIVE_REPORT.md` with problem framing, benchmark provenance, method/protocol, supported claims, weakened/rejected claims, failure taxonomy, figure/table inventory, limitations, and the intended paper scope.
   - Read `research/LITERATURE_GROUNDING.json` and carry forward the recent-paper gap, classic anchors, trend signals, and required baselines into the narrative. Do not write the paper as if literature/news grounding happened when the literature-grounding requirement is not satisfied. Trend signals can motivate but cannot replace paper/code/benchmark or local-result evidence for technical claims.
   - If the intended claim cannot be positioned against both recent high-quality papers and classic anchors, mark the narrative `pivot` or `blocked` instead of inventing a contribution.
   - If the evidence is only pilot-scale, explicitly label the narrative `pilot-note` or `short/workshop` scale.
   - Update `research/PIPELINE_STATE.json` so the analysis and narrative stages are `done` only when their artifacts exist.

8. Verify outputs:
   - Run the analysis script from a clean shell.
   - Confirm every generated figure/table file exists and is non-empty.
   - Self-audit the literature-grounding requirement before drafting so weak literature grounding cannot become a confident related-work section.
   - Self-audit the idea-provenance requirement and the code-reuse requirement before drafting so an agent-invented idea or untracked external code cannot become the paper's contribution.
   - Self-audit the image-2 figure requirements before drafting and again after LaTeX integration so conceptual figures cannot bypass image-2 or silently switch to a cleaned PDF/vector derivative.
   - Run `python -m argus_skill.skills.pipeline_contracts refresh-manifest --project-root .` after generation, then self-audit the artifact-manifest requirements (canonical sources, SHA-256 digests, TSV schemas, source links).
   - If the manifest reports digest drift, TSV schema mismatch, generated-source cycles, or unknown sources, fix the generator and rerun it before updating `PIPELINE_STATE.json`.
   - Quote the command output in the final verification block.

## Response shape
- Provide a compact table of generated artifacts.
- Include the regeneration command and raw evidence paths.
- State any claims that must be softened because evidence is incomplete.
