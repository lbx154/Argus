---
name: "FigureSpec: drawing exact diagrams from JSON"
description: "Use Choosing how to draw a research figure first; prepare exact graph geometry or a local vector component inside the selected Method D/B native PPT composition through PPT Master. FigureSpec is not a standalone framework-figure route; quantitative plots keep SciencePlots/Matplotlib."
---

# FigureSpec: drawing exact diagrams from JSON

> Adapted from ARIS `figure-spec` skill (MIT, © 2026 wanshuiyin).
> Renderer script copied verbatim into `figure_spec_scripts/` beside this skill.

## Scope

Use FigureSpec for a small exact topology, neighborhood, or geometric component
whose node positions and connections come from the method or source data.
`research-visualization-router.md` chooses the main figure route first.
Conceptual and framework figures use `paper-framework-figure-studio.md`:
Method D by default, with direct native PPT Method B when an image interface is
unavailable. This renderer supplies geometry or a component to that composition.
It does not replace the framework with its default boxes and arrows, and an
editable SVG alone does not satisfy the native PowerPoint source requirement.

Quantitative data, metric, result, and uncertainty plots stay on the existing
SciencePlots/Matplotlib route. ECharts can supply a genuine data component in a
PPT figure. A schematic neighborhood is not a measured result; preserve the
original graph data or mark the example as schematic.

## Source and renderer

The JSON specification is the editable source. Rendering the same specification
produces deterministic SVG; keep it as an internal component. Edit the source
specification when its geometry changes, then rebuild the component and the
affected native PPT objects. Ordinary data plotting and PPT's internal SVG
conversion remain available; there is no separate SVG framework workflow.

The renderer is `figure_spec_scripts/figure_renderer.py`, shipped beside this
skill. Set `ARGUS_FIGURE_COMPONENT_DIR` to the assigned candidate's private
component directory and save its `spec.json` there first. Resolve the renderer's
real path and use the same path for every command:

```bash
RENDER=$(find "$ARGUS_SKILL_HOME" . -name figure_renderer.py \
  -path '*figure_spec_scripts*' 2>/dev/null | head -1)
"${ARGUS_SKILL_PYTHON:-python3}" "$RENDER" schema
"${ARGUS_SKILL_PYTHON:-python3}" "$RENDER" validate "$ARGUS_FIGURE_COMPONENT_DIR/spec.json"
"${ARGUS_SKILL_PYTHON:-python3}" "$RENDER" render "$ARGUS_FIGURE_COMPONENT_DIR/spec.json" \
  --output "$ARGUS_FIGURE_COMPONENT_DIR/neighborhood.svg"
```

A small schematic geometry example is:

```json
{
  "title": "Schematic neighborhood geometry",
  "canvas": {"width": 320, "height": 180},
  "style": {
    "font_family": "Arial",
    "font_size": 16,
    "bg_color": "#FFFFFF"
  },
  "nodes": [
    {"id": "query", "label": "q", "x": 70, "y": 90,
     "width": 36, "height": 36, "shape": "circle",
     "fill": "#EEF6F3", "stroke": "#008F7A"},
    {"id": "a", "label": "a", "x": 240, "y": 45,
     "width": 36, "height": 36, "shape": "circle",
     "fill": "#FFFFFF", "stroke": "#3C5488"},
    {"id": "b", "label": "b", "x": 240, "y": 135,
     "width": 36, "height": 36, "shape": "circle",
     "fill": "#FFFFFF", "stroke": "#3C5488"}
  ],
  "edges": [
    {"from": "query", "to": "a", "color": "#008F7A"},
    {"from": "query", "to": "b", "color": "#ACB6C4", "style": "dashed"}
  ]
}
```

Use actual method labels and edge meanings in a paper; this syntax example is
not an architecture template. The renderer clips endpoints to their source and
target boundaries but does not route around unrelated nodes. Choose coordinates
with clear connector space; Graphviz may help calculate it. Inspect the
component's topology, labels, and scaling, then compose its exact nodes and
connectors with the other scientific objects in native PPT through PPT Master.
Do not route a failed component into a browser text-card framework instead.

## Return the component to its owner

A figure worker reviews only the assigned component or candidate against the
brief, then returns its source, preview, and evidence. It never writes the
parent manuscript, formal figures, or `paper/REVIEW.md`. The main Engineer
selects and merges the candidate, exports the matching vector PDF, checks the
included figure at publication size, and completes the current turn. The host's
formal Reviewer then judges the complete paper. Do not spawn an
integrated/full-paper Reviewer or another paper-wide review loop.

The complete framework keeps a canonical native PPTX and matching PDF/PNG.
Only the chosen components enter its source tree. Include the final PDF with
`\includegraphics`; an ordinary pdfLaTeX build does not directly accept SVG.
Keep already approved compositions and spend the main effort on the science.
