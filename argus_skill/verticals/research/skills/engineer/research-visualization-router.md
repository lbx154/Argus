---
name: "Choosing how to draw a research figure"
description: "Choose a renderer that faithfully represents the evidence for each paper figure; Review makes the strict visual judgment."
---

# Choosing how to draw a research figure

Use this in Paper before creating a figure. Choose the renderer from the
figure's semantics, then keep its source and final included export. For Method D,
also retain the actual API blueprint and prompt with the source.

## What Figure 1 must show

Every complete paper needs a real Figure 1 that communicates the problem,
mechanism, and claim-bearing flow at a glance. Embed an exported PDF, SVG, or
high-resolution PNG through `\includegraphics` or `\includesvg`; a boxed
paragraph or table inside a figure environment does not count.

## Choose from what the figure needs to express

| Need | Route |
|---|---|
| Any paper data/metric/result chart, including uncertainty or ablation | Matplotlib/SciencePlots through Styling data figures for publication |
| Conceptual, method pipeline, architecture, taxonomy, or teaser figure | Composing a conceptual paper figure: default Method D, image-API blueprint followed by editable native PPTX through PPT Master; Method B local vector drawing is the fallback |
| Exact load-bearing topology | Preserve exact connections during D reconstruction; FigureSpec, Draw.io, Graphviz, or browser SVG can supply exact subcomponents or the Method B fallback |
| Rich browser composition | Self-contained HTML/CSS/SVG rendered with `research_visual_scripts/browser_render.py` as a local vector tool, not a competing default for concept figures |
| Non-claim-bearing illustrative asset | image-2 only when configured; compose it inside an editable deterministic figure |

Topology fidelity takes priority over decorative richness. A polished Figure 1
does not need depth, icons, or decorative complexity. Never use generated image
text or geometry for scientific labels, arrows, values, or branch conditions.

For conceptual figures, open `paper-framework-figure-studio.md` for the canonical
Method D / Method B contract, including reference inspection, authorized image
generation, editable reconstruction, and explicit fallback disclosure. Do not
silently choose local drawing while describing it as API-assisted.
For a Method B method pipeline, `research-svg-pipeline.md` and
`python -m argus_skill.verticals.research.pipeline_figure` remain available:
the active model designs a compact horizontal SVG, and the tool crops it,
verifies Times New Roman, and exports a vector PDF without image credentials
or PPT software. These renderer-specific requirements do not apply to Method D.
Draw only when needed; reuse an existing suitable figure or blueprint across
rounds. Include the method overview PDF after Introduction, preferably on page
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
strict visual assessment and integrated review find that the paper holds.
