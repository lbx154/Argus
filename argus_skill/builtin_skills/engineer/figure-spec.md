---
name: "Figure Spec (deterministic SVG)"
description: "After the Research Visualization Router selects a simple exact-topology route, generate deterministic editable SVG architecture, workflow, pipeline, or audit-cascade diagrams from structured JSON. Do not select FigureSpec directly for visually rich paper conceptual/method figures; compare installed PPT Master and HTML/SVG routes first."
---

# Figure Spec — deterministic JSON → SVG renderer

> Adapted from ARIS `figure-spec` skill (MIT, © 2026 wanshuiyin).
> Renderer script copied verbatim to
> `argus_skill/builtin_skills/engineer/figure_spec_scripts/figure_renderer.py`.

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

Renderer:
`argus_skill/builtin_skills/engineer/figure_spec_scripts/figure_renderer.py`

CLI:
```bash
python figure_renderer.py render spec.json --output paper/figures/arch.svg
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
  "width": 800,
  "height": 500,
  "style": {
    "font_family": "Arial",
    "font_size": 14,
    "palette": ["#2563EB", "#10B981", "#EA580C"]
  },
  "nodes": [
    {"id": "planner", "label": "Planner", "x": 100, "y": 200,
     "shape": "rounded", "color": 0},
    {"id": "engineer", "label": "Engineer", "x": 350, "y": 100,
     "shape": "rounded", "color": 1},
    {"id": "reviewer", "label": "Reviewer", "x": 350, "y": 300,
     "shape": "rounded", "color": 2}
  ],
  "edges": [
    {"from": "planner", "to": "engineer", "label": "task"},
    {"from": "engineer", "to": "reviewer", "label": "evidence"},
    {"from": "reviewer", "to": "planner", "label": "verdict",
     "style": "dashed"}
  ],
  "groups": [
    {"id": "harness", "label": "Harness (dumb pipes)",
     "nodes": ["planner"], "color": "#F3F4F6"}
  ]
}
```

Allowed: `shape ∈ {rect, rounded, circle, diamond, ellipse}`,
`style ∈ {solid, dashed, dotted}`,
`color` is either a palette index or a `#RRGGBB` hex.

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

### Step 5 — optional reviewer audit

For the paper's main architecture figure, hand the rendered SVG + the
spec JSON to a reviewer agent (gpt-5.5 via `reviewer` route) with the
prompt:

```
Review this architecture figure. Spec attached as JSON; rendered SVG
attached. Check:
1. Does the figure clearly communicate the data flow described in
   `paper/sec/method.tex`?
2. Are any boxes labeled in a way the body text doesn't define?
3. Is anything load-bearing in the figure absent from the spec
   (i.e. drawn in spec.json but unused in main.tex)?
4. Greyscale legibility?
Return a list of concrete spec edits; reviewer rules on whether to
gate on each.
```

Note: the reviewer rules on whether the figure is publication-ready,
not the harness. This skill produces deterministic output; the
quality call lives with the agent.

## Design patterns

Common spec shapes that work well — copy then adapt:

- **Layered architecture** — nodes in horizontal rows (y = 100/200/300),
  edges flow downward
- **Hub-and-spoke** — one central node + radial edges
- **Pipeline with feedback** — left-to-right edges plus one dashed
  return edge
- **Audit cascade** — vertical stack of nodes, each with a "verdict"
  edge to a side column

The renderer handles arrow placement, label positioning, and
group-box rendering automatically; the spec only declares topology.

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
- Optionally register the spec/renderer in `FIGURE_PROVENANCE.json` for handoff.
- Add the SVG to LaTeX with `\includegraphics{figures/<name>.svg}`
  (most modern TeX engines handle SVG directly; for older toolchains,
  convert to PDF with `inkscape --export-type=pdf`)
