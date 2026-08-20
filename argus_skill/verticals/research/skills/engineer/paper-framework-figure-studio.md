---
name: "Paper Framework Figure Studio"
description: "Design and audit a publication-grade Figure 1 teaser, method, framework, architecture, or taxonomy before rendering it with PPT Master, HTML/SVG, FigureSpec, Draw.io, Mermaid/Graphviz, or optional image-2. Renderer-neutral S0-S7 workflow; use after the Research Visualization Router identifies a conceptual paper figure."
---

# Paper Framework Figure Studio

This is the renderer-neutral design workflow for a research paper's Figure 1.
It carries the useful design stages from
`paper-framework-figure-studio-pro-v3.1.4a` without coupling them to image-2.
The Research Visualization Router chooses the renderer only after the figure's
facts, reader path, layout, labels, caption, and audit contract are defined.

Do not skip directly from "we need a diagram" to drawing boxes. Do not use
generic placeholders. Read the actual paper and evidence first.

## S0 — Freeze the factual contract

Read the current research brief, manuscript, method source, claim/evidence map,
and results report. Record:

- exact module/component names;
- input, output, data flow, control flow, and arrow directions;
- the load-bearing contribution and its visible internal steps;
- baseline/status-quo path and proposed path;
- evidence anchors and the claim boundary;
- facts that must not appear or must not be invented.

The core contribution cannot be an empty box. Show its mechanism with nested
cards, an inset, a loop, or a compact internal panel.

## S1 — Choose the reader path

Write one sentence stating what a reader should understand in five seconds.
Choose a figure grammar that supports it:

- horizontal input → mechanism → output/evidence;
- nested offline/online or training/inference containers;
- central method with baseline and evidence side panels;
- multi-panel A/B/C for problem, mechanism, and outcome;
- taxonomy or explanatory geometry for survey/theory work.

Decide what belongs in pixels, caption, legend, and body text. The figure carries
structure and reader path; the caption carries definitions, caveats, and detail.

## S2 — Explore layout directions

Sketch at least two materially different layouts in a lightweight design spec.
Stop once one direction clearly wins; do not grind out variants.

Useful patterns include central hero, horizontal swimlanes, nested containers,
hub-and-spoke, zig-zag pipeline, compact research poster, grayscale-accent,
color-coded phases, and overlapping A/B/C panels. Reject layouts with weak
hierarchy, large dead areas, crossing arrows, or repeated identical boxes.

## S3 — Select the structural direction

Choose the layout that best satisfies:

1. paper fidelity;
2. core-mechanism visibility;
3. immediate reader path;
4. compact information density;
5. editability and reliable final-size export.

Record why the rejected direction was weaker so later revisions do not repeat it.

## S4 — Co-design figure, caption, and body callout

Freeze a candidate contract containing:

- exact title and visible labels;
- source-backed nodes and arrows;
- color/shape legend;
- caption plan;
- the sentence in the manuscript that calls out Figure 1;
- the claim boundary that remains visible;
- final physical width and target export format.

Use actual project terminology. Never expose raw paths, code identifiers, daemon
terms, GPU IDs, or generic labels such as "quality gate" when the paper names a
specific mechanism.

## S5 — Render through the selected deterministic or generative route

Return to the Research Visualization Router and use one renderer:

- PPT Master for rich editable composition and native PPTX;
- HTML/SVG for bespoke exact layouts;
- FigureSpec, Draw.io, Mermaid/Graphviz for explicit topology;
- image-2 only when configured and semantically appropriate.

Preserve editable source and export a real SVG/PDF/PNG for the manuscript.
Rendering must be deterministic whenever the chosen route supports it.

### Renderer-neutral design system

- Landscape, paper-width composition with one dominant reading direction.
- Clean Figma-like grouped modules; rounded cards only where grouping benefits.
- Warm white or white background; dark-gray strokes; restrained low-saturation
  accents with redundant shape/line encoding.
- Strong title and section hierarchy; short labels; no paragraph-sized text.
- Consistent connectors and arrowheads behind nodes.
- Compact but not crowded; minimal decorative icons and no logo wall.
- No heavy gradients, glassmorphism, photorealism, stock art, heavy shadows,
  sketch fonts, arbitrary blobs, dashboard chrome, or marketing decoration.
- At final paper width, every label must remain readable without zooming.

Reference tokens for a 1536×1024-class canvas: background `#fbfaf7`, stroke
`#1f2933`, 2 px; corner radius 10–16 px; card gap 12–24 px; title 38–52 px,
section headers 22–30 px, card labels 16–22 px. Scale proportionally for SVG.

## S6 — Integrate the figure-text bundle

Embed the exported asset with `\includegraphics` or `\includesvg`, add a
substantive caption and label, and reference it in the body. Rebuild the paper.
The source, render, caption, and manuscript terminology must agree.

A LaTeX table, boxed paragraph, or `\rule` bars inside a `figure` environment
are not a Figure 1 render.

## S7 — Joint final audit

Inspect the rendered PDF at actual page size and verify:

| Check | Pass condition |
|---|---|
| Paper fidelity | Names and arrows match the manuscript and evidence |
| Core mechanism | Contribution internals are visible, not an empty box |
| Reader path | The intended five-second takeaway is obvious |
| Label accuracy | No invented, clipped, tiny, or inconsistent labels |
| Visual hierarchy | Proposed mechanism dominates; support material recedes |
| Figure-text split | Figure shows structure; caption explains detail |
| Claim boundary | Unsupported scope is not implied visually |
| Print quality | Legible in grayscale and at final paper width |

Return `PASS`, `TEXT-REPAIR`, `RENDER-REPAIR`, or `DIRECTION-REPAIR`. Repair the
editable source and rerender; never patch only the exported SVG/PDF.
