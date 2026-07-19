---
name: Research Results Analysis And Figures
description: Turn raw experiment outputs into evidence-grounded tables, claims, and paper figures without inventing missing numbers. Use the research vertical's unified visualization router for every visual.
category: research-analysis
version: 2
created_at: 2026-07-19T00:00:00+00:00
---

# Research Results Analysis and Figures

Convert completed runs into canonical derived results and a reviewer-auditable
visual story. Analysis source code and raw artifacts remain authoritative;
paper prose and figures are derived views.

## 1. Inventory and qualify evidence

Build a source table covering every claim-relevant JSON/JSONL/TSV/CSV, run
manifest, verifier output, and log:

- artifact path, run ID, condition, public source/split, metric, timestamp;
- completion/verifier state, seeds/repeats, budget/configuration;
- data-quality notes, exclusions, failed and negative runs.

Do not count benchmark manifests, task declarations, or `status.task_count` as
executed evidence. A final claim needs completed scored rows for every required
method and baseline condition. Label pilots and diagnostics explicitly.

For compared rows verify compatible source, split/cohort, metric, evaluator,
model/backend, and budget. Preserve failed/null outcomes unless the experimental
plan gave a valid exclusion rule.

## 2. Build one reproducible analysis program

Prefer `paper/analysis/build_results.py` or an equivalent source-controlled
pipeline. It must:

- read canonical raw artifacts rather than hard-coded paper numbers;
- normalize schemas and reject rows with missing/extra declared fields;
- compute aggregates, uncertainty, significance tests, and failure slices;
- write deterministic tables and figure-source bundles;
- regenerate every downstream result from a clean shell.

Use `Paper Chart Styling` for ordinary matplotlib charts. For each other figure,
load the research-only `Research Visualization Router`; do not select a renderer
from old image-2 wording.

## 3. Produce canonical result artifacts

As applicable:

```text
paper/artifacts/results_table.tsv
paper/artifacts/main_results_matrix.tsv
paper/artifacts/failure_taxonomy.tsv
paper/artifacts/claims_evidence.tsv
paper/artifacts/result_to_claim.tsv
paper/CLAIM_GRAPH.json
paper/EVIDENCE_GAPS.json
paper/RESULTS_REPORT.md
research/NARRATIVE_REPORT.md
paper/figures/FIGURE_PROVENANCE.json
```

The main matrix should expose public source, evaluation unit, system/model,
method/control, metric, budget/configuration, result, uncertainty, and raw
artifact. Do not force a cross-benchmark matrix when the scientific design has a
different natural evidence shape.

Map every planned claim to `supported`, `weak`, `rejected`, `missing`, or
`contradicted`. Missing evidence becomes a named experiment, ablation, robustness
slice, or claim downgrade—not an estimate.

## 4. Route and build figures

For every figure write a brief: claim, reader takeaway, role, canonical inputs,
final physical size, uncertainty, editability, and forbidden invention.

Then use `Research Visualization Router`:

- data/result charts normally use matplotlib;
- Vega/ECharts/Recharts/Plotly/HTML are valid when their semantics add value and
  they follow the fixed browser-render contract;
- exact topology uses FigureSpec, Mermaid/Graphviz, or Draw.io;
- slide-like editable composition may use PPT Master;
- image-2 is optional and selected only when configured and scientifically
  appropriate.

Optionally record renderer/source metadata in `FIGURE_PROVENANCE.json` when it
helps later repair. This metadata is not a paper-readiness gate. Image-2 outputs
may additionally retain `IMAGE2_FIGURES.json`.

Each figure needs a stable ID/filename, claim binding, source/input hashes,
renderer, regeneration command, dimensions, review artifact, caption plan,
LaTeX label, and in-text reference plan.

## 5. Statistical and visual discipline

- Report mean and dispersion for repeated runs.
- Use tests appropriate to the design; otherwise mark significance N/A.
- Keep units and axis scales explicit; never truncate or transform silently.
- Use colorblind-safe redundant encoding and inspect at final single/double
  column size.
- Keep body figures purposeful; move low-value diagnostics to the appendix.
- A figure may simplify presentation, never alter scientific meaning.
- Every completed optimizer-step training run cited in analysis retains its own
  reward/loss/gradient/KL/entropy/throughput curves from that run's logs.

## 6. Write the result and narrative handoff

`paper/RESULTS_REPORT.md` states:

- what the data supports, weakens, rejects, or leaves unresolved;
- headline values with canonical source paths;
- uncertainty, significance, ablations, failures, and boundary conditions;
- where the method loses or trades one metric for another, without spin;
- exact missing evidence and claim wording changes.

`research/NARRATIVE_REPORT.md` carries problem framing, literature gap,
protocol, supported/rejected claims, limitations, and the intended figure/table
inventory. Internal paths, commands, GPU/cache details, route names, hashes, and
daemon mechanics stay in provenance artifacts—not manuscript prose.

## 7. Refresh and verify

```bash
python -m argus_skill.skills.pipeline_contracts refresh-manifest \
  --project-root .
python -m argus_skill.skills.pipeline_contracts refresh-artifact-freshness \
  --project-root .
```

Run the analysis from a clean shell. Confirm every table and figure exists and
is current against the claim graph and manuscript.
Only then advance analysis/narrative state in `research/PIPELINE_STATE.json`.
