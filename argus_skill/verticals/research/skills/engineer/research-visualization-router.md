---
name: "Research Visualization Router"
description: "Use before rendering any research-paper visual. Route data figures through SciencePlots and make the LiveFigure-style procedural native-PPTX workflow the default for conceptual/method/architecture figures. Keep exact-topology and optional image-2 routes as explicit exceptions."
---

# Research Visualization Router

One figure contract, many renderers. Choose from the figure's semantics and the
capabilities actually available; never force image-2 merely because an old
template named it, and never fake image-2 provenance when no image route exists.

## Figure 1 is a paper deliverable

Every submission-quality research paper must have a reader-facing Figure 1
teaser, method/framework overview, architecture, or taxonomy that communicates
the problem, core mechanism, and claim-bearing flow at a glance. It must be a
real exported SVG/PDF/PNG embedded by `\includegraphics` or `\includesvg`.
A LaTeX table, boxed paragraph, or `\rule` bars inside a `figure` environment do
not count. Use the LiveFigure-style procedural native-PPTX workflow through PPT
Master by default. image-2 is optional and never required for this route.

Before rendering Figure 1, open and execute the renderer-neutral
`Paper Framework Figure Studio` S0-S7 workflow. It owns factual extraction,
reader path, layout exploration, caption co-design, the shared visual design
system, manuscript integration, and final-size audit. This Router then selects
the concrete renderer; renderer skills do not replace the Studio design pass.

This Argus synthesis draws on permissively licensed official workflows:

- Vega CLI deterministic SVG/PDF/PNG export (BSD-3-Clause).
- Apache ECharts SVG SSR and ARIA/decal guidance (Apache-2.0).
- Recharts seeded examples and visual-regression workflow (MIT).
- Plotly Kaleido static export (MIT).
- Playwright screenshot regression controls (Apache-2.0).
- Observable Plot structural SVG snapshots (ISC).
- PPT Master editable DrawingML workflow (MIT).

## 1. Probe capability without reading secrets

```bash
"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --model-api-status
"${ARGUS_SKILL_BIN:-argus-skill}" --ppt-master-status
```

Use the reported `image` and `image_review` availability. Do not inspect the
capability vault or infer availability from prose. An unavailable image route is
not a project blocker when a truthful deterministic renderer can express the
same research content.

The PPT Master status is independent of model API status. It succeeds only when
the pinned toolkit is complete, clean, and has dependencies recorded for the
active Python. A successful status means PPT Master is usable even when every
image route is unavailable. Read its adapter and upstream routing workflow
before choosing a renderer. If status fails, continue with another truthful
deterministic route rather than blocking the paper. Prefer browser-rendered HTML
for a rich fallback or Draw.io/FigureSpec for exact topology, preserve the
editable source, and record why native PPTX was unavailable.

## 2. Write the figure brief before choosing a tool

For each planned figure record:

- paper claim and intended reader takeaway;
- role: data/result, method/process, architecture/topology, qualitative example,
  explanatory concept, teaser, or interactive supplement;
- authoritative inputs and exact labels;
- target venue, final physical width, vector/raster requirement, and editability;
- uncertainty that must remain visible;
- acceptable transformations and forbidden invention.

The Engineer chooses the renderer. The Reviewer judges whether that choice
communicates the evidence and fits the paper; the harness only verifies files,
hashes, and provenance.

## 3. Route by semantics

| Figure need | Preferred route |
|---|---|
| Any paper data/metric/result chart | SciencePlots/Matplotlib + Paper Chart Styling; export PDF/SVG/PNG from one script |
| Interactive or browser-only research supplement (not a paper data figure) | Vega-Lite/Vega, ECharts, Recharts, or Plotly with pinned deterministic export |
| Bespoke HTML/D3/Observable Plot | Native SVG plus structural snapshot and browser screenshot |
| Conceptual/method/architecture/teaser figure, including Figure 1 | LiveFigure-style procedural native PPTX through installed PPT Master; retain semantic contract, layout plans, editable PPTX, rendered review page, and paper exports |
| Exact load-bearing topology, arrow direction, branch condition, or spelling | Editable deterministic PPT Master, browser SVG, Graphviz/Draw.io, or FigureSpec when its routes have clear space |
| Simple exact topology in a supporting figure | FigureSpec, Mermaid/Graphviz, or Draw.io only when native graph editing or topology fidelity matters more than the LiveFigure-style composition |
| Expressive visual blueprint or optional icon asset | image-2 when configured and evidence-faithful; redraw scientific labels, arrows, numbers, and claim-bearing geometry in the native PPTX |
| Visual that inherently requires unavailable generative media | Mark blocked or redesign the claim; never fabricate an output |

When correctness depends on exact node-edge incidence, arrow direction, branch
conditions, or label spelling, topology fidelity takes priority over decorative
richness. Use image-2 only when connector geometry is not load-bearing or for
non-semantic illustrative material.

### LiveFigure-style conceptual-figure contract

For every conceptual, method, architecture, teaser, or graphical-abstract
figure, use this default pipeline:

```text
evidence-bound semantic contract
→ same-domain exemplar/style retrieval
→ at least two materially different layout plans
→ procedural editable PPTX generation with native objects
→ PDF/PNG review render
→ static geometry and text-overflow checks
→ visual critic at final paper size
→ bounded source-level repair
→ PDF + SVG + high-DPI PNG paper exports
```

- Keep exact labels, arrows, numeric claims, forbidden invention, and evidence
  anchors in a machine-readable semantic contract.
- Retrieve exemplars only as layout and style evidence. Record their source and
  never copy protected artwork or unsupported scientific content.
- Build the accepted composition as native PowerPoint shapes, groups, text, and
  connectors. A full-slide screenshot inside a PPTX is a failure.
- Use a pure white `#ffffff` paper canvas by default. Put low-saturation colour
  inside semantic modules; do not use a dark or tinted full-canvas background
  unless the manuscript itself uses that background.
- Render the PPTX before review. Check overflow, clipping, overlap, connector
  attachment, reading order, contrast, and final-size legibility.
- Give the visual critic the rendered page and semantic contract. Repair the
  editable source, never only the PDF/PNG derivative, and stop after three
  source-level repair rounds unless the direction itself is rejected.
- image-2 may suggest composition or supply a non-claim-bearing icon, but it
  must not be the source of scientific text, arrows, numbers, or final
  claim-bearing geometry.

Figure 1 must pass the full Studio design and final-size audit. It may use a
sparse deterministic topology renderer when that is the clearest and most
faithful grammar; publication polish does not require depth, icons, or
decorative complexity.

Hand-authoring raw SVG is not on this table. For data figures it bypasses the
required SciencePlots source-data pipeline; for conceptual figures it produces
a whiteboard sketch instead of using a renderer with layout and type handling.
Pick the route that matches the figure semantics.

Give each figure its own subagent, carrying the brief, the canonical numbers and
the chosen renderer, so one drawing gets one undivided attempt.

Do not introduce React, Vega, Plotly, or raw SVG for a paper data plot. Do not
use a dashboard screenshot as a paper figure. Do not use SciencePlots for a
non-data conceptual or method diagram merely because it is installed; keep the
LiveFigure-style conceptual-figure route for those figures.

## 4. Browser-render contract

Keep browser figures self-contained under `paper/figures/src/<figure_id>/`:

```text
index.html or src/
data.{json,csv}
package.json + lockfile
local fonts/assets
render command
```

Requirements:

- Pin Node, browser, chart library, locale, timezone, viewport, DPR, and random
  seed. No CDN or runtime network assets.
- Use fixed numeric dimensions and disable animation/transitions.
- ECharts uses `renderer: "svg"` or SVG SSR where supported; import its ARIA
  component and use decal/marker redundancy.
- Recharts must receive seeded data and `isAnimationActive={false}`.
- Mark the final root `data-figure-root data-figure-ready="true"` only after
  fonts and chart layout are complete.
- Choose the output by how the figure was built. `--output *.svg` extracts an
  `<svg>` that already exists in the page, so it fits a chart library's own
  output (Vega, ECharts, D3). A figure laid out in CSS has no such element, and
  `--output *.pdf` is its vector route — LaTeX includes PDF directly. Use PNG
  only when Canvas or raster content is essential. Asking for `.svg` from a CSS
  layout fails with `figure root contains no SVG`; that error means the wrong
  output extension, never that you should go and write the SVG by hand.

Seed the packaged renderer into the project with the vertical skills, then run:

```bash
# The renderer ships beside this skill. Resolve it from the skill directory
# rather than guessing a package path:
RENDER=$(find "$ARGUS_SKILL_HOME" . -name browser_render.py \
  -path '*research_visual_scripts*' 2>/dev/null | head -1)
python "$RENDER" \
  --input paper/figures/src/<id>/index.html \
  --selector '[data-figure-root]' \
  --output paper/figures/<id>.pdf \
  --width 1200 --height 720
```

The renderer blocks remote requests, waits for fonts and the readiness marker,
fails on browser/console errors, and writes `<output>.render.json`.
Install the optional driver in the project environment with
`pip install 'argus-skill[visual-web]'` or `pip install playwright`; then either
run `python -m playwright install chromium` or pass `--browser-channel chrome`
when a compatible system Chrome is already installed.

## 5. Review at final use size

For every route:

1. Render from clean source.
2. Confirm dimensions/viewBox, labels, units, exact edge incidence, arrow
   direction, branch labels, node-boundary termination, and file integrity.
3. Inspect at the actual single- or double-column size.
4. Check for foreign-node penetration, connector/text intersections, overlap,
   clipping, avoidable crossings, minimum rendered type, grayscale/CVD
   readability, and redundant encoding.
5. For browser output, retain a normalized SVG/HTML structural snapshot and a
   Playwright screenshot; pixel diffs are meaningful only under the same pinned
   browser, OS, fonts, DPR, and headless mode.
6. For PDF run `pdffonts`; for raster verify effective DPI.
7. Reviewer compares the figure to its claim, source data, caption, and paper
   context. A visually attractive but unsupported edge/value is a failure.

## 6. Optional renderer handoff metadata

When useful for later repair or reproducibility, register renderer metadata:

```bash
python -m argus_skill.verticals.research.figure_provenance register \
  --project-root . \
  --figure-id <id> \
  --role <data|method|architecture|teaser|qualitative|other> \
  --renderer <free-form truthful renderer name> \
  --source <authoritative spec/script/prompt> \
  --output paper/figures/<file> \
  --input <canonical data or supporting artifact> \
  --review <review artifact> \
  --render-metadata <render sidecar> \
  --command '<exact regeneration command>'
```

Then run:

```bash
python -m argus_skill.verticals.research.figure_provenance validate \
  --project-root .
```

The optional manifest is `paper/figures/FIGURE_PROVENANCE.json`. It is not a
completion or anti-cheat gate, and Reviewer must not reject an otherwise good
figure merely because metadata is absent. Renderer names are intentionally
open-ended. Legacy
`IMAGE2_FIGURES.json` remains valid image-2-specific evidence, and
`figure_tool sync-paper-metadata` also registers the accepted raster in the
renderer-neutral manifest when present.

## 7. Renderer-specific honesty

- **image-2:** preserve prompt, raw sidecar, inspect/review, accepted raster hash,
  and legacy manifest. Never resave the accepted raster behind its hashes.
- **Data chart:** preserve canonical data and executable plotting source. Never
  hard-code paper numbers or use visual interpolation as data.
- **HTML/React:** preserve source, frozen data, lockfile, local assets, render
  metadata, SVG/PDF/PNG, and regression evidence.
- **LiveFigure-style PPT Master:** preserve the semantic contract, retrieved
  exemplar ledger, rejected and accepted layout plans, upstream route artifacts,
  editable PPTX, rendered review pages, critic findings, source-level repairs,
  and final paper exports.
- **Diagrams:** every load-bearing node and edge must trace to code, evidence,
  documentation, or an explicit hypothesis label.

Do not spend repeated rounds polishing metadata or minor visual preferences.
Once the actual rendered figure is readable, coherent, factually correct, and
good-looking enough, move on.
