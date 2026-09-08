---
name: "Choosing how to draw a research figure"
description: "Choose a renderer that faithfully represents the evidence for each paper figure; Review makes the strict visual judgment."
---

# Choosing how to draw a research figure

Use this in Paper or during figure repairs in Review. Conceptual and method figures use
default Method D with Method B as the fallback, through
`paper-framework-figure-studio.md`. Keep the editable source and final included
export. Quantitative plots remain on their data-figure route.

## What Figure 1 must show

Every complete paper needs a real Figure 1 that communicates the problem,
mechanism, and claim-bearing flow at a glance. Embed an exported PDF, SVG, or
high-resolution PNG through `\includegraphics` or `\includesvg`; a boxed
paragraph or table inside a figure environment does not count.

## Choose from what the figure needs to express

| Need | Route |
|---|---|
| Any paper data/metric/result chart, including uncertainty or ablation | Matplotlib/SciencePlots through Styling data figures for publication |
| Method pipeline or architecture overview | Composing a conceptual paper figure: default Method D blueprint and editable native PPTX through PPT Master; Method B direct native PPT design is the fallback |
| Mathematical operators, bounds, or geometric reasoning | Native PPT shapes, equation objects, and mathematical text runs within Method D or B |
| Other conceptual or teaser figure | Composing a conceptual paper figure: default Method D, with Method B fallback when unavailable or unsuitable |
| Exact load-bearing topology | FigureSpec or Graphviz can supply exact coordinates; compose the final nodes and connectors in native PPT |
| Data chart component in a PPT figure | ECharts with actual data and fixed dimensions; inspect the vector export in the final PPT |
| Non-claim-bearing illustrative asset | image-2 only when configured; compose it inside an editable deterministic figure |

Topology fidelity takes priority over decorative richness. A polished Figure 1
does not need depth, icons, or decorative complexity. Never use generated image
text or geometry for scientific labels, arrows, values, or branch conditions.

For method and conceptual figures, open `paper-framework-figure-studio.md`
before drawing. Method D is the default: learn from suitable reference figures,
obtain an actual image design blueprint, reconstruct exact editable objects
through PPT Master, and export the paper figure. Use its refined academic
palette, thin strokes, whitespace, and real mathematical typography.

Method B is the fallback when the configured image route or required Method D
prerequisites are unavailable or the task's constraints rule it out. Compose
directly in native editable PPT through the installed PPT Master; do not pause
or ask the operator for image API setup. `academic-vector-figures.md` covers
precise math and chart components. There is no separate SVG workflow. SVG can remain an
internal asset format of a selected renderer. Reuse suitable figures across
rounds and name the actual workflow used without inventing a blueprint.
Include the vector PDF after Introduction, preferably on page 2 or 3; changing
float placement does not require redrawing the figure.

## Shared requirements

- Start from a one-sentence takeaway and authoritative data or method sources.
- Match every label, value, unit, connection, and arrow direction to evidence.
- Keep source data and executable plotting or editable drawing source beside the
  final export.
- Use a restrained color-blind-safe palette and readable publication-size type.
- Prevent overlap, clipping, connector penetration, misleading scales, and
  avoidable crossings.
- Make the caption explain definitions and interpretation rather than repeat the
  graphic.

For browser figures, keep assets local, disable animation, use fixed dimensions,
and render the existing SVG or a PDF:

```bash
RENDER=$(find "$ARGUS_SKILL_HOME" . -name browser_render.py \
  -path '*research_visual_scripts*' 2>/dev/null | head -1)
python "$RENDER" \
  --input paper/figures/src/<id>/index.html \
  --selector '[data-figure-root]' \
  --output paper/figures/<id>.pdf \
  --width 1200 --height 720
```

An SVG output requires an SVG in the page; a CSS composition should export PDF
rather than trigger `figure root contains no SVG`.

Paper is responsible for complete figures and a successful manuscript compile,
not a separate visual check. During final Review, inspect every page and every
figure at actual publication size; repair the source and rerender until the
strict visual assessment and integrated review find that the paper holds.
