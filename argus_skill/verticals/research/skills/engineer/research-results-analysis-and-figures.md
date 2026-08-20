---
name: "Research Results Analysis And Figures"
description: "Turn raw experiment outputs into evidence-grounded tables, claims, and paper figures, and route every visual across PPT Master, HTML/SVG, ECharts, Recharts, Vega, FigureSpec, matplotlib, or optional image-2. Use PPT Master as a first-class polished editable route for non-data paper figures when installed; never invent missing numbers."
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
model/backend, and budget. Preserve failed/null outcomes in the canonical audit
record unless the experimental plan gave a valid exclusion rule.

## 2. Build one reproducible analysis program

Prefer `paper/analysis/build_results.py` or an equivalent source-controlled
pipeline. It must:

- read canonical raw artifacts rather than hard-coded paper numbers;
- normalize schemas and reject rows with missing/extra declared fields;
- compute aggregates, uncertainty, significance tests, and failure slices;
- derive ranking/winner names, caption values, and figure claim text from the
  same analysis objects as the plotted marks; never hard-code an expected
  method, ordering, or conclusion in a figure spec;
- write deterministic tables and figure-source bundles;
- regenerate every downstream result from a clean shell.

When comparative ordering drives a paper claim and the generator is reusable or
otherwise easy to get subtly wrong, prefer a small counterfactual regression:
change only a copied fixture so one ordering reverses, then confirm the
regenerated caption, claim metadata, table, and visual reverse consistently.
This is risk-based engineering evidence, not a required project artifact or
completion gate; Reviewer decides whether the current source/data/output bundle
already provides enough confidence. Never mutate or exclude real experiment
rows for this check.

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

For a selected idea, weak core evidence first triggers diagnosis and improvement,
not paper writing. Audit whether the mechanism received a faithful implementation
and competitive test, make concrete targeted repairs while they have credible
information gain, and rerun the decisive comparison. Predeclare what the repair
should change and preserve the earlier runs. Never manufacture improvement by
changing labels, dropping seeds, switching headline metrics after inspection, or
mining a favorable slice.

Then select the publication thesis. The paper is not the evidence inventory or a
chronological experiment report:

- keep every claim-critical comparison that could change the thesis;
- lead with the strongest valid evidence that establishes and explains the thesis;
- keep misconfigured runs out of scientific conclusions;
- move secondary dead ends and exhaustive diagnostics to internal artifacts or
  an appendix when useful;
- return to research/plan if no independently valuable thesis survives.

## 4. Route and build figures

For every figure write a brief: claim, reader takeaway, role, canonical inputs,
final physical size, uncertainty, editability, and forbidden invention.

Before analysis handoff, render the paper's Figure 1 teaser/framework overview.
For a method or system paper it should show the problem/input, the load-bearing
mechanism or architecture, and the output/evidence path. For a theory or survey
paper use an explanatory geometry, taxonomy, or conceptual map. Preserve an
editable source and export a real SVG/PDF/PNG that the draft embeds. A LaTeX
table, boxed paragraph, or `\rule` bar display inside a `figure` environment is
not Figure 1. If no image route exists, deterministic rendering is mandatory,
not a blocker: use PPT Master, HTML/SVG, FigureSpec, Draw.io,
Mermaid/Graphviz, or another route selected below.
Run the renderer-neutral `Paper Framework Figure Studio` S0-S7 workflow before
authoring the chosen renderer's source; the Router alone is not a design brief.

Then use `Research Visualization Router`:

- data/result charts normally use matplotlib;
- Vega/ECharts/Recharts/Plotly/HTML are valid when their semantics add value and
  they follow the fixed browser-render contract;
- polished conceptual, method, architecture, teaser, or graphical-abstract
  composition should consider installed PPT Master first when visual hierarchy,
  icons, callouts, grouped modules, or editable design handoff matter;
- simple exact topology may use FigureSpec, Mermaid/Graphviz, or Draw.io after
  the router compares it with PPT Master and browser-native SVG;
- image-2 is optional and selected only when configured and scientifically
  appropriate.

Do not default to matplotlib for a non-data conceptual or method diagram merely
because it is installed. Matplotlib is the ordinary statistical-chart route,
not the universal fallback for paper graphics.

Optionally record renderer/source metadata in `FIGURE_PROVENANCE.json` when it
helps later repair. This metadata is not a paper-readiness gate. Image-2 outputs
may additionally retain `IMAGE2_FIGURES.json`.

Each figure needs a stable ID/filename, claim binding, source/input paths and versions,
renderer, regeneration command, dimensions, review artifact, caption plan,
LaTeX label, and in-text reference plan.

Before handoff, Reviewer should inspect the actual rendered figure at its final
physical size when the available tools support it; reading SVG/HTML/TikZ source
alone is weak visual evidence. Reuse the paper's normal render/layout review
rather than creating a separate mandatory review artifact. Integration means
the current paper or bounded report actually embeds/references the accepted
figure with its caption and body callout, not merely lists the output path in an
inventory table.

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
one-sentence thesis, supported/rejected claims, strongest accept/reject arguments,
limitations, and the intended figure/table inventory. It must not present a
method as the contribution while making that method's failure the main message
unless a separate, compelling insight supports that framing. Organize the
manuscript evidence by the questions needed to prove the thesis, not by run order
or by everything the project tried. Internal paths,
commands, GPU/cache details, route names, hashes, and daemon mechanics stay in
provenance artifacts—not manuscript prose.

## 7. Verify

Run the analysis from a clean shell. Confirm every table and figure exists and
is current against the claim graph and manuscript.
Only then advance analysis/narrative state in `.argus/PIPELINE_STATE.json`.
