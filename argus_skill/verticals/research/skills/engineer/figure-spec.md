---
name: "Figure Spec (deterministic SVG)"
description: "After the Research Visualization Router selects a simple exact-topology route, generate deterministic editable SVG architecture, workflow, pipeline, or audit-cascade diagrams from structured JSON. Do not select FigureSpec directly for visually rich paper conceptual/method figures; compare installed PPT Master and HTML/SVG routes first."
---

# Figure Spec — deterministic JSON → SVG renderer

> Adapted from ARIS `figure-spec` skill (MIT, © 2026 wanshuiyin).
> Renderer script copied verbatim into `figure_spec_scripts/` beside this skill.

## When to use this renderer

Use FigureSpec when architecture, workflow, audit cascade, ER/dependency graphs,
labels, colors, and arrow targets must be exact and editable. The Research
Visualization Router decides whether FigureSpec, browser SVG, diagrams, PPT
Master, data-chart tooling, or image-2 best fits a paper figure.

The two skills are complementary; both can live in the same paper.
Data/metric/result plots stay with matplotlib (the existing
`research-results-analysis-and-figures` skill covers those).

## Core properties

- **Deterministic** — running the renderer twice on the same spec
  produces byte-identical SVG. Critical for reproducibility.
- **Editable** — output SVG opens cleanly in Inkscape / Illustrator
  for last-mile polish.
- **No AI in the loop** — the spec → SVG step is pure code; only the
  **drafting of the spec** is an LLM task.

## Tool location

Renderer: `figure_spec_scripts/figure_renderer.py`, shipped beside this
skill. Resolve it rather than guessing a package path:

```bash
RENDER=$(find "$ARGUS_SKILL_HOME" . -name figure_renderer.py \
  -path '*figure_spec_scripts*' 2>/dev/null | head -1)
python "$RENDER" render spec.json --output paper/figures/arch.svg
python figure_renderer.py validate spec.json
python figure_renderer.py schema
```

## Workflow

### Step 1 — understand the diagram goal

Engineer drafts a short brief: what the figure communicates, who reads
it, what the takeaway is. Constraints (column width vs full-page,
greyscale vs color) matter for the spec.

### Step 2 — draft the FigureSpec JSON

The schema:

```json
{
  "title": "Argus Research Factory Architecture",
  "canvas": {"width": 800, "height": 500},
  "style": {
    "font_family": "Arial",
    "font_size": 14,
    "palette": ["#2563EB", "#10B981", "#EA580C"]
  },
  "nodes": [
    {"id": "planner", "label": "Planner", "x": 100, "y": 200,
     "shape": "rounded", "fill": "#DBEAFE", "stroke": "#2563EB"},
    {"id": "engineer", "label": "Engineer", "x": 350, "y": 100,
     "shape": "rounded", "fill": "#D1FAE5", "stroke": "#10B981"},
    {"id": "reviewer", "label": "Reviewer", "x": 350, "y": 300,
     "shape": "rounded", "fill": "#FFEDD5", "stroke": "#EA580C"}
  ],
  "edges": [
    {"from": "planner", "to": "engineer", "label": "task"},
    {"from": "engineer", "to": "reviewer", "label": "evidence"},
    {"from": "reviewer", "to": "planner", "label": "verdict",
     "style": "dashed"}
  ],
  "groups": [
    {"id": "harness", "label": "Harness (dumb pipes)",
     "node_ids": ["planner"], "fill": "#F3F4F6", "stroke": "#9CA3AF"}
  ]
}
```

Allowed: `shape ∈ {rect, rounded, circle, diamond, ellipse}`,
`style ∈ {solid, dashed, dotted}`. Nodes and groups use explicit `fill` and
`stroke` colors.

### Step 3 — render and validate

```bash
python figure_renderer.py validate spec.json   # schema-only check
python figure_renderer.py render spec.json --output paper/figures/arch.svg
```

If validation fails the renderer prints structured errors with
JSON-pointer paths so the engineer can fix the spec directly.

### Step 4 — visual review

Open the SVG. Check:
- All node labels readable at intended print size
- No edge crossing through a node
- Color palette consistent with paper figures
- Greyscale-readable if the venue prints greyscale

If any of these fail, edit the spec (NOT the SVG — the SVG is the
output, the spec is the source of truth) and re-render.

### Final review ownership

Do not launch a separate Reviewer from Paper. The checks above are ordinary
engineering validation needed to produce a complete compilable draft. During
Review, the assigned read-only visual pass inspects the rendered SVG at final
paper size together with the spec and manuscript, and the integrated Reviewer
decides whether the repaired paper is publication-ready.

## Design patterns

Common spec shapes that work well — copy then adapt:

- **Layered architecture** — nodes in horizontal rows (y = 100/200/300),
  edges flow downward
- **Hub-and-spoke** — one central node + radial edges
- **Pipeline with feedback** — left-to-right edges plus one dashed
  return edge
- **Audit cascade** — vertical stack of nodes, each with a "verdict"
  edge to a side column

The renderer clips edge endpoints to source and target boundaries, positions
labels, and renders groups. It does not obstacle-route around unrelated nodes.
Use FigureSpec only when each declared straight/curved edge has clear space;
otherwise reposition nodes or choose Graphviz, Draw.io, browser SVG, or PPT
Master. Always inspect the final render for connector penetration and overlap.

## Anti-patterns

- ❌ Using it automatically for every teaser/conceptual figure — first route by
  semantics and available capability through Research Visualization Router.
- ❌ Hand-editing the SVG — your changes are lost the next time
  someone re-renders. Edit the spec.
- ❌ Embedding arbitrary inline SVG / raster in a node — keep the spec abstract;
  if raster content is essential, return to the router.
- ❌ Using this for data plots — matplotlib already covers that
  better

## Output contract

- Renders to `paper/figures/<name>.svg`
- Spec lives at `paper/figures/<name>.spec.json` so future re-renders
  are reproducible and visible in `git diff`
- Add the SVG to LaTeX with `\includegraphics{figures/<name>.svg}`
  (most modern TeX engines handle SVG directly; for older toolchains,
  convert to PDF with `inkscape --export-type=pdf`)
