---
name: "Research SVG Pipeline"
description: "Synthesize a compact, horizontal ICLR-style method pipeline from executed research code and the current manuscript, with staggered SVG geometry and Times New Roman."
---

# Research SVG Pipeline

Use this as the default for a method, architecture, or pipeline figure in Paper,
and for a concrete pipeline repair in Review. You, the active Engineer model,
design the figure from the research; the tool handles cropping, real font
verification, and SVG/PDF export. It needs no image-generation service.

## Call only when a figure is needed

This is an on-demand drawing component, not a per-round hook or a new stage.
First inspect the existing figure and its source. Reuse a suitable SVG/PDF;
normally design each figure once. Do not call `brief`, redraw, or rerender just
because another writing round, narrative edit, compile, or Review has started.
Only a method change, an explicit user request or a concrete figure defect
justifies revising it. For a geometry-only repair, edit the source and render;
reuse the already-read code/paper context. Review normally inspects the existing
PDF. Moving its float in LaTeX does not require regenerating the graphic.

## Ground the drawing

Read the current manuscript and the executed method code, including the direct
configuration or call site needed to understand branches. Paper may use
`HANDOFF.md` to locate these sources; Review follows the manuscript's direct
dependencies. Do not crawl research history or replace the scientific method
with the Argus orchestration lifecycle.

Gather a bounded design brief from explicitly selected files:

```bash
python -m argus_skill.verticals.research.pipeline_figure brief \
  --project-root . --paper paper/main.tex --paper paper/sections/method.tex \
  --code src/model.py --code src/train.py
```

Replace the example paths with real current files. Include each relevant TeX
section explicitly; the tool does not guess dependencies or silently truncate
source. Read its output, resolve code/prose disagreements, and identify the
input, transformation, novel mechanism, output, and any actual training loop.

## Compose the SVG yourself

Write `paper/figures/src/method_pipeline.svg`. This is a model-authored drawing,
not a row-of-boxes template. Use the ICLR visual style regardless of the paper's
selected venue; keep the venue's own manuscript format.

For a real architecture overview, first inventory the important components,
interfaces, stored state and feedback paths in the code. Preserve that coverage
in the drawing: use compact bands and nested groups, exposing the mechanism
inside each major module. Distinguish control, execution, record/memory and
domain layers when the implementation does. Show dependencies, artifact flow,
library access and actual verification/repair loops through explicit connections.
A four-box summary with large blank interiors is insufficient for a complex
system. Density should come from relevant subcomponents and relationships;
do not invent modules or decorative detail to fill space. Expand canvas height
while keeping the main flow horizontal when this preserves readable type.

- Arrange the dominant flow horizontally, with tightly grouped modules and
  staggered heights. Use compact branches, nested operations, tokens, matrices,
  or one meaningful example where they explain the actual mechanism.
- Remove surplus outside margins and internal gaps. Avoid large title bands,
  empty panels, stretched arrows and equally sized boxes full of blank space.
  Compactness must preserve label and connector legibility.
- Use **Times New Roman everywhere**, including small annotations and formulas.
  At the final publication width every label must remain at least 8 pt.
- Give the contribution one restrained accent; keep inherited operations quiet.
  Use vector primitives and short exact scientific labels, without decorative
  shadows, 3D, stock icons or invented measurements.
- Plan arrow ports and route between boundaries around unrelated shapes/text.
  Separate real feedback/training edges from the forward pass with dashed lines.
- Use one direct `<g id="pipeline-content">` for all visible geometry. Put
  `<defs>`, `<title>`, `<desc>` and optional `<style>` outside it. Keep transforms
  on child groups, not the content group. Use an explicit `viewBox`, absolute
  coordinates, live `<text>`/`<tspan>`, and small arrowheads (up to 8 SVG units).
  Do not add a canvas-sized background rectangle; the exporter supplies white.
- Keep the SVG self-contained: no raster, external dependencies, scripting,
  animation, filters, CSS transforms, percent geometry or outlined text.

## Render and include

The renderer needs the existing `visual-web` extra, Chromium, and installed
Times New Roman. If needed, install the optional renderer in the project
environment with `pip install 'argus-skill[visual-web]'` and
`python -m playwright install chromium`. Install a legitimately available copy
of Times New Roman in the OS font directory. The renderer reports actual font
substitution instead of silently exporting Liberation Serif.

```bash
python -m argus_skill.verticals.research.pipeline_figure render \
  --input paper/figures/src/method_pipeline.svg \
  --output paper/figures/method_pipeline.svg --pdf --png --width 624
```

`--width` is the intended printed width in CSS pixels: 624 is 6.5 inches, 336 is
3.5 inches. Match the available width in the actual author kit. The tool crops
to the content, adds a small safety margin, forces Times New Roman on labels,
checks the fonts actually used for glyphs, and exports vector SVG/PDF plus a
PNG preview. If labels become too small, simplify the composition or enlarge
them in the source; do not misstate the final width to bypass the check.

Open the PNG and inspect it at final size. Repair internal whitespace, wrong
arrows, collisions and awkward grouping in the editable SVG, then rerender.
The geometry checks are authoring aids, not a scientific or visual verdict.

```latex
% After the final paragraph of Introduction, before the next section.
\begin{figure}[!t]
  \centering
  \includegraphics[width=\linewidth]{figures/method_pipeline.pdf}
  \caption{The method's scientific takeaway and explanation of the flow.}
  \label{fig:method-pipeline}
\end{figure}
```

Default to inserting the **PDF** after the end of Introduction, with a top float
on page **2 or 3**. Check the compiled PDF and adjust the float location in LaTeX
if needed. Respect the actual Introduction length and author kit; do not add
blank pages, force a page break or shrink text merely to hit a page number.
This is a preferred placement, not an independent acceptance step.

Adapt the caption to the actual paper and its drafting contract; use `figure*`
if a two-column paper needs full width. Use the exported PDF for normal
pdfLaTeX builds; plain `\includegraphics` does not load SVG. Compile and inspect
the included figure. Keep the source and final exports, with no extra reports
or acceptance files. Strict visual and scientific acceptance stays in the
existing integrated Review, recorded in `paper/REVIEW.md`.
