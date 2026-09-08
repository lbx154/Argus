---
name: "Choosing how to draw a research figure"
description: "Choose a renderer that faithfully represents the evidence for each paper figure; Review makes the strict visual judgment."
---

# Choosing how to draw a research figure

Use this in Paper before creating a figure. Choose the renderer from the
figure's semantics, then create only its source and final included export.

## What Figure 1 must show

Every complete paper needs a real Figure 1 that communicates the problem,
mechanism, and claim-bearing flow at a glance. Embed an exported PDF, SVG, or
high-resolution PNG through `\includegraphics` or `\includesvg`; a boxed
paragraph or table inside a figure environment does not count.

## Choose from what the figure needs to express

| Need | Route |
|---|---|
| Any paper data/metric/result chart, including uncertainty or ablation | Matplotlib/SciencePlots through Styling data figures for publication |
| Method pipeline or architecture overview | Composing a conceptual paper figure: design the composition first, then use editable native PPTX through PPT Master and export a vector PDF |
| Other conceptual or teaser figure | Composing a conceptual paper figure; editable native PPTX through PPT Master when appropriate |
| Exact load-bearing topology | FigureSpec, Draw.io, Graphviz, or browser SVG |
| Rich browser composition | Self-contained HTML/CSS/SVG rendered with `research_visual_scripts/browser_render.py` |
| Non-claim-bearing illustrative asset | image-2 only when configured; compose it inside an editable deterministic figure |

Topology fidelity takes priority over decorative richness. A polished Figure 1
does not need depth, icons, or decorative complexity. Never use generated image
text or geometry for scientific labels, arrows, values, or branch conditions.

For the default method/architecture route, open `paper-framework-figure-studio.md`
before drawing. Use its composition and publication style with the installed
PPT Master toolkit (`python -m argus_skill.tools.ppt_master status`). Keep native
editable shapes/text in the PPTX and include its vector PDF in the manuscript.
PPT Master may use SVG as its authoring intermediate; keep the composition,
typographic hierarchy and grouped modules through conversion.

`research-svg-pipeline.md` remains available for a specifically requested SVG
workflow or a repair to an existing suitable SVG. It is an exporter, not a
replacement for composition design. Reuse suitable figures across rounds.
Include the PDF after Introduction, preferably on page 2 or 3; a LaTeX placement
adjustment does not require redrawing the figure.

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
