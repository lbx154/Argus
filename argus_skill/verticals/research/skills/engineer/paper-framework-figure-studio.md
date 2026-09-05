---
name: "Paper Framework Figure Studio"
description: "Create one publication-ready conceptual figure from the current paper and direct evidence, composed the way strong published figures are."
---

# Paper Framework Figure Studio

Use this in Paper for Figure 1 or another conceptual, method, architecture, or
taxonomy figure. Read `HANDOFF.md`, the current manuscript, the executed method,
and direct result sources. Create only the editable figure source and the final
export included by the paper.

## Choose a composition archetype first

Strong published figures reuse a small set of compositions. Pick the one that
fits the paper's actual claim before drawing anything:

| Archetype | Use when | Structure | Exemplars |
|---|---|---|---|
| Pipeline strip | The contribution is a method with a traceable forward pass (the default) | One horizontal band: input at far left, two to four enclosed modules, output at far right; training or feedback signals drawn as visually distinct arrows over the flow; stages may be numbered and walked in order by the caption | RAG, InstructGPT, DreamFusion |
| Contrast diptych | The contribution is best stated as a delta against a standard approach | Two panels, old left and new right, drawn as the same diagram differing in exactly one visible attribute — a deleted box, a changed loss, one added matrix; the method panel may get more area | DPO, Chain-of-Thought, ReAct |
| Lineage progression | The contribution generalizes a known paradigm | Three lettered panels: two familiar paradigms, then the contribution in the terminal position; panel letters cited from the body text | VAR |
| Overview plus zoom | The novelty lives inside one block of an otherwise standard pipeline | Panel (a): the full pipeline at cartoon level showing where the block sits; panel (b): the single novel unit magnified with its internal wiring and dimensions | Stable Diffusion 3, NSA |
| Results-first teaser | The strongest claim is empirical | Figure 1 carries no architecture: a sample grid, a filmstrip contrast, or one headline plot with a bold takeaway sentence opening the caption; the mechanism moves to Figure 2 | VAR, Genie, Rho-1 |
| Coverage map | Benchmark, dataset, or evaluation papers | A color-coded taxonomy tree, spectrum bar, or specimen grid whose legend marks which parts are new; the caption carries most of the explanation | DecodingTrust, Aya |

## Design

1. State the figure's one-sentence scientific takeaway.
2. List the exact modules, labels, and connections, including each connection's
   source, target, direction, boundary port, and meaning.
3. Make the contribution unmistakable through subtraction or one minimal
   difference wherever possible — delete a box the baseline needs, mark the
   inherited parts frozen, change one token — so the baseline diagram is one
   visual edit away from yours. When subtraction is impossible, use exactly one
   highlighting device: terminal panel position, one reserved accent color
   against a muted base, an ours-versus-existing legend, or extra area. Render
   standard inherited machinery in quiet gray; a figure where everything is
   equally loud says nothing.
4. Keep color semantic: one color means one concept, identically in every panel
   and matched to the results charts. If a legend line cannot state what a
   color means, remove the color. Stay within about six categorical colors,
   color-blind safe, and legible in grayscale.
5. Budget on-canvas text by role and architectural depth: keep module labels
   short, but expose the important internal components, interfaces, and feedback
   in a complex system. Do not impose a fixed word cap that erases its mechanism.
   Use compact nested groups and additional horizontal bands when needed;
   preserve readable type and move explanatory prose into the caption.
6. Where it helps comprehension, run one concrete example through the diagram —
   an actual input and its intermediate artifacts — rather than only abstract
   labels.
7. Write the caption to stand alone: open with the takeaway (bold it when the
   venue style allows), walk the panels in reading order, decode every color,
   symbol, and badge, and name the contrast explicitly. Reuse panel letters and
   stage numbers as anchors in the body text. Never caption a figure "System
   architecture."

## Geometry and typography

- one entry point and one exit, with a single dominant left-to-right reading
  direction; return or training arrows are the sanctioned exception and must
  look different (dashed or a distinct color);
- connectors terminate at explicit node boundaries; no shaft or arrowhead
  enters an unrelated node, label, or panel; if arrows must cross, fix the
  layout rather than the arrows;
- one shape class per concept, used identically everywhere; every element in
  one step persists visibly into the next or its removal is the labeled action;
- annotate real dimensions where they matter and mark arbitrary counts with an
  ellipsis or a multiplier, so drawn counts are never accidentally readable as
  exact;
- no decorative 3D or gradients: every visual property either encodes a
  declared meaning or stays neutral;
- set the canvas to the final single- or double-column width before drawing,
  keep text at or above eight points at that size, and export vector;
- gloss any named component a general reviewer may not know — no bare acronym
  in a box;
- every visible name, direction, and value matches the paper and executed
  method verbatim; regenerate the figure when notation changes.

## Production route

Never generate the figure as a raster image in one shot: emit an editable
structured source, render it, inspect the render, and revise until it passes.
Decompose complex figures — build panels and modules separately, then compose.

| Composition | Primary route |
|---|---|
| Pipeline strip or method architecture | Research SVG Pipeline (`research-svg-pipeline.md`): model-authored compact horizontal SVG grounded in code and paper, staggered geometry, Times New Roman, cropped vector PDF export |
| Contrast diptych, lineage panels | Editable native objects through PPT Master; for a contrast diptych draw one diagram and apply the delta programmatically so the panels are guaranteed identical except the edit |
| Panels of verbatim text (prompts, trajectories, rubrics) | HTML/CSS with inline SVG rendered headlessly to vector PDF — the only route with a real text-layout engine; verify the render visually since headless failures are silent |
| Exact load-bearing topology, taxonomy trees | Graphviz for layout coordinates, restyled through SVG; or FigureSpec, Draw.io, browser SVG |
| Results teaser | Matplotlib through Paper Chart Styling |

Inspect every render at actual publication size against the design rules above:
reading direction, one highlighting device, decodable legend, text budget,
notation match, font size, no crossings, and a caption with takeaway, panel
walk, and color decode.

Paper needs a complete, credible figure and a successful compile. Do not create
layout reports, exemplar collections, provenance records, or visual-review
files. Strict page-by-page visual acceptance happens once, in Review.
