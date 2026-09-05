---
name: "Research Visualization Router"
description: "Choose a direct, evidence-faithful renderer for each paper figure and leave strict visual acceptance to Review."
---

# Research Visualization Router

Use this in Paper before creating a figure. Choose the renderer from the
figure's semantics, then create only its source and final included export.

## Figure 1 is a paper deliverable

Every complete paper needs a real Figure 1 that communicates the problem,
mechanism, and claim-bearing flow at a glance. Embed an exported PDF, SVG, or
high-resolution PNG through `\includegraphics` or `\includesvg`; a boxed
paragraph or table inside a figure environment does not count.

## Route by semantics

| Need | Route |
|---|---|
| Any paper data/metric/result chart, including uncertainty or ablation | Matplotlib/SciencePlots through Paper Chart Styling |
| Method pipeline or architecture overview | Research SVG Pipeline: synthesize compact horizontal SVG from the current code and paper, with staggered layout and Times New Roman |
| Other conceptual or teaser figure | Paper Framework Figure Studio; editable native PPTX through PPT Master when appropriate |
| Exact load-bearing topology | FigureSpec, Draw.io, Graphviz, or browser SVG |
| Rich browser composition | Self-contained HTML/CSS/SVG rendered with `research_visual_scripts/browser_render.py` |
| Non-claim-bearing illustrative asset | image-2 only when configured; compose it inside an editable deterministic figure |

Topology fidelity takes priority over decorative richness. A polished Figure 1
does not need depth, icons, or decorative complexity. Never use generated image
text or geometry for scientific labels, arrows, values, or branch conditions.

For the default method-pipeline route, open `research-svg-pipeline.md` and use
`python -m argus_skill.verticals.research.pipeline_figure`. The active model
designs the SVG; the tool crops outside whitespace, verifies Times New Roman,
and exports a vector PDF for the manuscript. Image-generation credentials and
PPT software are unnecessary for this route.
Use it only when the figure actually needs drawing; reuse an existing suitable
SVG/PDF across rounds. Include the PDF after Introduction, preferably on page
2 or 3; a LaTeX placement adjustment does not require redrawing the figure.

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
strict visual pass and integrated review accept the paper.
