---
name: "Turning results into tables and figures"
description: "Turn raw outputs into tables and figures using native PPT Master or SciencePlots/Matplotlib, with optional HTML/SVG, ECharts, Recharts, Vega, or FigureSpec components inside the chosen route."
---

# Turning results into tables and figures

Read the executed code, explicit configuration, raw outputs, evaluator results,
and the current research notes in `RESEARCH_NOTES.md`. Produce only analysis code, paper tables, editable
figure sources, and final exports used by `paper/main.tex`.

Conceptual, method, and architecture figures follow
`paper-framework-figure-studio.md`: **Method D is the default**, using an actual
image blueprint and editable PPT Master reconstruction; **Method B is the
fallback** using direct native PPT design when D is
unavailable or unsuitable. Do not introduce a separate SVG workflow.

Scientific analysis is the main work. For a full paper, aim for three figures
and include at least two distinct, informative scientific figures. Usually these
explain the mechanism, show the main comparison, and resolve a mechanism or
scope question through an ablation, diagnostic, or generalization result. Use
existing evidence, not fabricated or duplicate panels to fill a count.

Delegate substantial drawing or layout work in parallel using the isolated
candidate workflow in `paper-framework-figure-studio.md`; continue analysis and
decisive experiments in the main task. The lead Engineer owns selection and
promotion into the manuscript. Reuse a selected, checked composition; refresh
affected values from new raw rows without repeatedly redesigning the figure.

## Analysis

- Compute every paper number from raw rows; never hard-code an expected result.
- Before aggregating, match raw configuration/repeat identities and counts to
  the declared run, account for failures and exclusions, and verify the
  claim-critical invariants. A copied completion marker or a previously
  generated table cannot make a partial or changed run complete. Keep data,
  code/configuration, and summaries from the same validated attempt together.
- Compare compatible data, models, budgets, evaluators, and uncertainty.
- Prefer a small counterfactual regression when it directly tests whether a
  result or figure changes under a claim-critical input change.
- Preserve valid losing rows in raw evidence, but build the paper around the
  positive thesis that clears the Paper entry bar.
- Reviewer decides whether the evidence supports the claim.

## Figures

- Use the single SciencePlots/Matplotlib data-figure path for quantitative paper
  charts.
- Use Composing a conceptual paper figure (`paper-framework-figure-studio.md`)
  for method/architecture pipelines: design meaningful groups and visual
  hierarchy from code and paper, expose the real mechanism with informative
  objects and internal relationships, then use PPT Master with an editable PPTX
  and included vector PDF. Bring the alignment and component consistency of a
  strong Figma design without replacing the mechanism with text cards or
  empty decorative space.
- Keep other conceptual figures on the same native PPT route. ECharts,
  Recharts, Vega, or HTML/SVG can supply an internal data component; FigureSpec
  may supply coordinates. They do not create a separate framework workflow.
- Use real measured values, correct units, conventional axes, readable labels,
  and uncertainty when scientifically relevant.
- Make the winning comparison and takeaway immediately visible.
- For diagrams, preserve exact semantic geometry and prevent connector
  penetration, overlap, clipping, and ambiguous direction.

The main Engineer embeds the selected claim-bearing tables and figures in
`paper/main.tex`; a figure worker returns only its isolated candidate and local
checks. After the round's scientific and figure work is complete, the main
Engineer returns to the host, which invokes the formal Reviewer on the whole
current paper. Do not dispatch a native integrated/full-paper Reviewer,
duplicate that paper-wide review, or write the main `paper/REVIEW.md`.
