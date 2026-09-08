---
name: "Turning results into tables and figures"
description: "Turn raw outputs into paper tables and figures using PPT Master, HTML/SVG, ECharts, Recharts, Vega, FigureSpec, or the single SciencePlots/Matplotlib approach for data figures."
---

# Turning results into tables and figures

Read the executed code, explicit configuration, raw outputs, evaluator results,
and the current research notes in `RESEARCH_NOTES.md`. Produce only analysis code, paper tables, editable
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
- For conceptual and method/architecture figures, follow
  `paper-framework-figure-studio.md`: default Method D (image-API blueprint,
  editable reconstruction through PPT Master), with Method B local vector
  drawing as the disclosed fallback. `research-svg-pipeline.md` is the Method B
  pipeline renderer, not the default for all method figures.
- HTML/SVG, ECharts, Recharts, Vega, and FigureSpec remain local composition
  tools where appropriate; they do not change the concept-figure default.
- Use real measured values, correct units, conventional axes, readable labels,
  and uncertainty when scientifically relevant.
- Make the winning comparison and takeaway immediately visible.
- For diagrams, preserve exact semantic geometry and prevent connector
  penetration, overlap, clipping, and ambiguous direction.

Embed every claim-bearing table and figure in `paper/main.tex`. Final
judgments about the science, figures, and language are made together in Review.
