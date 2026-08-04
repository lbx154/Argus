---
name: "Research Visualization Router"
description: "Use before rendering any research-paper visual. Select among installed PPT Master, HTML/SVG, ECharts, Recharts/React, Vega, Plotly, FigureSpec, diagrams, matplotlib, and optional image-2. Prefer PPT Master for polished editable non-data conceptual/method/architecture figures when available; image-2 is not required."
---

# Research Visualization Router

One figure contract, many renderers. Choose from the figure's semantics and the
capabilities actually available; never force image-2 merely because an old
template named it, and never fake image-2 provenance when no image route exists.

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
deterministic route rather than blocking the paper.

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
| Ordinary statistical/result chart | Matplotlib + Paper Chart Styling |
| Declarative, faceted, layered, or browser-parity chart | Vega-Lite/Vega; export with `vg2svg`/`vg2pdf` |
| Large/complex chart with ECharts semantics | ECharts SVG SSR; fixed width/height, animation off, ARIA + decals |
| Existing React/Recharts research component | Preserve it; fixed numeric dimensions, seeded data, animation off, browser export |
| Existing Plotly analysis | Kaleido with pinned Plotly/Kaleido/Chrome and local assets |
| Bespoke HTML/D3/Observable Plot | Native SVG plus structural snapshot and browser screenshot |
| Polished conceptual/method/architecture figure with visual hierarchy, icons, callouts, or grouped modules | Installed PPT Master; retain source SVG/design spec, editable PPTX, and rendered paper asset |
| Simple exact method/architecture topology | FigureSpec, Mermaid/Graphviz, or Draw.io after comparing the richer routes |
| Expressive conceptual teaser | image-2 when configured and evidence-faithful; otherwise installed PPT Master or deterministic HTML/SVG |
| Visual that inherently requires unavailable generative media | Mark blocked or redesign the claim; never fabricate an output |

Do not introduce React for a simple line plot. Do not use a dashboard screenshot
as a paper figure when the underlying SVG or declarative spec can be exported.
Do not default to matplotlib for a non-data conceptual or method diagram merely
because it is installed; use it for ordinary statistical charts.

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
- Prefer SVG. Use PDF for exact page/physical sizing and PNG only when Canvas or
  raster content is essential.

Seed the packaged renderer into the project with the vertical skills, then run:

```bash
python argus_builtin_skills/engineer/research_visual_scripts/browser_render.py \
  --input paper/figures/src/<id>/index.html \
  --selector '[data-figure-root]' \
  --output paper/figures/<id>.svg \
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
2. Confirm dimensions/viewBox, labels, units, arrow direction, and file integrity.
3. Inspect at the actual single- or double-column size.
4. Check grayscale/CVD readability and redundant encoding.
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
- **PPT Master:** preserve the upstream route artifacts, source SVG/design spec,
  editable PPTX, and rendered review pages.
- **Diagrams:** every load-bearing node and edge must trace to code, evidence,
  documentation, or an explicit hypothesis label.

Do not spend repeated rounds polishing metadata or minor visual preferences.
Once the actual rendered figure is readable, coherent, factually correct, and
good-looking enough, move on.
