---
name: "Paper Framework Figure Studio"
description: "Design and audit a publication-grade Figure 1 teaser, method, framework, architecture, or taxonomy with the default LiveFigure-style semantic-contract, exemplar, procedural native-PPTX, render, critic, and repair workflow."
---

# Paper Framework Figure Studio

This is the LiveFigure-style default design workflow for a research paper's
Figure 1 and other conceptual paper figures. It carries the useful design stages from
`paper-framework-figure-studio-pro-v3.1.4a` without coupling them to image-2.
The Research Visualization Router uses procedural native PPTX through PPT
Master unless an explicit exact-topology exception applies.

Do not skip directly from "we need a diagram" to drawing boxes. Do not use
generic placeholders. Read the actual paper and evidence first.

## S0 — Freeze the factual contract

Read the current research brief, manuscript, method source, claim/evidence map,
and results report. Record:

- exact module/component names;
- input, output, data flow, control flow, and arrow directions;
- an edge ledger with source, target, direction, meaning
  (`data`, `control`, `feedback`, `comparison`, or `evidence`), branch label,
  and authoritative source;
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

## S2 — Retrieve exemplars and explore layout directions

Retrieve a small set of same-domain open-paper figures or project-owned examples
for layout and style evidence. Record source, license when known, and the
specific reusable prior—such as hierarchy, spacing rhythm, or panel grammar.
Do not copy artwork or scientific content. Then sketch at least two materially
different layouts in a lightweight design spec. Stop once one direction clearly
wins; do not grind out variants.

Useful patterns include central hero, horizontal swimlanes, nested containers,
hub-and-spoke, zig-zag pipeline, compact research poster, grayscale-accent,
color-coded phases, and aligned non-overlapping A/B/C panels. Reject layouts with weak
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

## S5 — Generate procedural native PPTX

Use the installed PPT Master workflow to generate native editable PowerPoint
objects from the accepted semantic and layout plan:

- every module, label, connector, group, and callout remains independently
  editable;
- a whole-slide raster or screenshot embedded in PPTX is prohibited;
- keep a pure white `#ffffff` paper canvas by default;
- use FigureSpec, Draw.io, or Mermaid/Graphviz only for an explicitly documented
  supporting exact-topology exception;
- use image-2 only when configured, and only as a composition blueprint or
  non-claim-bearing asset.

Preserve the semantic contract, exemplar ledger, layout plans, editable PPTX,
and a real PDF/PNG review render. Export PDF, SVG, and high-DPI PNG paper assets
from the accepted source. Rendering must be deterministic whenever supported.

### Renderer-neutral design system

- Landscape, paper-width composition with one dominant reading direction.
- Aligned, non-overlapping grouped modules; rounded cards only where grouping benefits.
- Pure white `#ffffff` paper background; dark-gray strokes; restrained
  low-saturation accents inside semantic modules with redundant shape/line
  encoding. Never use a dark or tinted full-canvas background by default.
- Short labels and clear section hierarchy; omit an in-graphic title when the
  caption already identifies the figure; do not use paragraph-sized text.
- Draw only edges declared in the S0 ledger. Every arrowhead points toward its
  named target. Use one dominant reading direction and route feedback around
  the outside of the primary flow.
- Attach connectors to explicit node-boundary ports. An arrow tip may meet the
  target boundary, but no shaft or arrowhead may enter a node fill, cross an
  unrelated node or label, or pass through a panel boundary without meaning.
  Avoid crossings; reroute or use an unambiguous bridge when one is unavoidable.
- Use conventional scientific grammar: processes are rectangles, decisions are
  diamonds with labeled outgoing conditions, and enclosing boxes denote real
  scope. Do not add decorative arrows, arbitrary bidirectionality, or shape
  changes solely for variety.
- No node, label, legend, callout, or panel overlaps another. Preserve visible
  outer margins and inter-node clearance.
- Compact but not crowded; minimal decorative icons and no logo wall.
- No heavy gradients, glassmorphism, photorealism, stock art, heavy shadows,
  sketch fonts, arbitrary blobs, dashboard chrome, or marketing decoration.
- Use a neutral publication sans-serif and no more than two type weights. At
  final paper width, ordinary labels should be at least approximately 8 pt and
  secondary text at least 7 pt.

Reference tokens for a 1536×1024-class canvas: background `#ffffff`, stroke
`#1f2933`, 2 px; corner radius 10–16 px; card gap 12–24 px; title 38–52 px,
section headers 22–30 px, card labels 16–22 px. Scale proportionally for SVG.

## S6 — Integrate the figure-text bundle

Embed the exported asset with `\includegraphics` or `\includesvg`, add a
substantive caption and label, and reference it in the body. Rebuild the paper.
The source, render, caption, and manuscript terminology must agree.

A LaTeX table, boxed paragraph, or `\rule` bars inside a `figure` environment
are not a Figure 1 render.

## S7 — Render, critic, bounded repair, and joint final audit

Render the native PPTX to PDF/PNG, inspect it at actual page size, and verify:

| Check | Pass condition |
|---|---|
| Paper fidelity | Names and arrows match the manuscript and evidence |
| Edge incidence | Every declared edge appears once with the correct source, target, direction, meaning, and branch label |
| Connector integrity | Connectors terminate at node boundaries and never penetrate unrelated nodes, labels, or groups |
| Geometry | No overlap, clipping, avoidable crossing, or out-of-canvas element |
| Visual grammar | Process, decision, artifact, and scope shapes have conventional, consistent meanings |
| Final typography | Labels remain readable at final use size with a restrained type hierarchy |
| Core mechanism | Contribution internals are visible, not an empty box |
| Reader path | The intended five-second takeaway is obvious |
| Label accuracy | No invented, clipped, tiny, or inconsistent labels |
| Visual hierarchy | Proposed mechanism dominates; support material recedes |
| Figure-text split | Figure shows structure; caption explains detail |
| Claim boundary | Unsupported scope is not implied visually |
| Print quality | Legible in grayscale and at final paper width |

Return `PASS`, `TEXT-REPAIR`, `RENDER-REPAIR`, or `DIRECTION-REPAIR`. Repair the
editable source and rerender; never patch only the exported SVG/PDF. Apply at
most three source-level repair rounds before returning to layout selection.
