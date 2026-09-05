---
name: "Research Results Analysis And Figures"
description: "Turn raw outputs into direct paper tables and figures through PPT Master, HTML/SVG, ECharts, Recharts, Vega, FigureSpec, or the single SciencePlots/Matplotlib data-figure path."
---

# Research Results Analysis and Figures

Read the executed code, explicit configuration, raw outputs, evaluator results,
and current `HANDOFF.md`. Produce only analysis code, paper tables, editable
figure sources, and final exports used by `paper/main.tex`.

## Analysis

- Compute every paper number from raw rows; never hard-code an expected result.
- Compare compatible data, models, budgets, evaluators, and uncertainty.
- Prefer a small counterfactual regression when it directly tests whether a
  result or figure changes under a claim-critical input change.
- Preserve valid losing rows in raw evidence, but build the paper around the
  positive thesis that clears the Paper entry bar.
- Reviewer decides whether the evidence supports the claim.

## Figures

- Use the single SciencePlots/Matplotlib data-figure path for quantitative paper
  charts.
- Use Research SVG Pipeline (`research-svg-pipeline.md`) for method/architecture
  pipelines: synthesize compact horizontal, staggered SVG from code and paper,
  with Times New Roman and an included vector PDF.
- Use PPT Master, HTML/SVG, ECharts, Recharts, Vega, or FigureSpec for other conceptual
  and interactive-source figures when appropriate.
- Use real measured values, correct units, conventional axes, readable labels,
  and uncertainty when scientifically relevant.
- Make the winning comparison and takeaway immediately visible.
- For diagrams, preserve exact semantic geometry and prevent connector
  penetration, overlap, clipping, and ambiguous direction.

Embed every claim-bearing table and figure in `paper/main.tex`. Final
scientific, visual, and language acceptance happens together in Review.
