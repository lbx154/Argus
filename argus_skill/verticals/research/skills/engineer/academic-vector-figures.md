---
name: "Academic vector figures and mathematical typography"
description: "Prepare precise mathematical typography and data components for native editable PPT paper figures."
---

# Academic vector figures

Use inside Figure Studio's default Method D reconstruction or its Method B
direct PPT fallback after selecting a composition. Both routes keep the main
figure in native editable PowerPoint objects through PPT Master. This page
covers precise components, not an alternative whole-figure drawing workflow.
The active Engineer keeps the project's configured model.

## Work in the final physical size

Measure the manuscript's inclusion width first. A 5.5-inch figure is 396 PDF
points wide. Work at that width, with ordinary labels 8–9 pt, panel headings
9–10 pt, 0.5–0.9 pt strokes, white background, and one or two semantic accents.
Use aligned whitespace and small panel labels; a figure does not need a card
around every operator or a large title repeating its caption.

The provided palette uses deep navy `#3C5488` and muted teal `#008F7A` against
white, with charcoal-blue text and gray rules. Keep fills very pale and reserve
full color for meaningful marks, short headings, or the scientific contrast.
Muted terracotta `#C17664` is an alternative contrast accent, not a third color
to add everywhere. The palette is inspired by restrained scientific publishing
styles; a familiar palette name alone does not make a composition refined.

Prefer representations that explain the mechanism: token sequences, sparse
rows, interval glyphs, geometric bounds, aligned comparison panels, and actual
data plots. Small geometry must stay schematic unless backed by real values;
use ellipses where drawn counts are arbitrary and explain the abstraction.
Build enough of the real structure to make the contribution visible: preserve
important internal operations, interfaces, and before/after relationships.
Use reusable native groups and careful alignment for Figma-level precision.
Resolve sparse layouts by exposing missing scientific structure or tightening
the composition; resolve crowding through grouping and concise labels. Neither
generic prose cards nor decorative filler supplies scientific detail.

## Native PPT mathematical typography

Use native equation objects or text runs with real subscript/superscript
baselines. Keep mathematical variables in a compatible math face and prose in
the diagram's sans-serif face. Use actual Greek, set, and relation glyphs;
inspect their font coverage and spacing in the exported PowerPoint render.
Do not replace notation with programming-style labels such as `C_t`.

Give a formula its own measured baseline and bounding box. Align equalities,
indices, and repeated symbols consistently across panels. Move a derivation to
the caption when the panel needs smaller type to contain it. A mechanism figure
usually needs the decisive expression, not every equation from the method.
Keep the source notation and the matching native PPT objects together so a
notation correction cannot silently leave an old export in the paper.

Use reusable PowerPoint groups for sequences, codewords, intervals, and
candidate sets. The object geometry must encode the actual mechanism. If
drawn counts are illustrative, use an ellipsis or an explicit schematic label.
Do not use a decorative plot to suggest a numerical result that was not measured.

## Data components inside the PPT

ECharts can render a real data panel when its layout and marks help the figure.
Use local data and assets, `animation: false`, an explicit size, and the SVG
renderer. Match its palette, type, axis weight, and markers to the surrounding
PPT. Keep axis labels and units, uncertainty definitions, and actual scales.
Retain the option object and input data alongside the source.

Export the chart as a vector component through the existing browser renderer
and compose it using PPT Master's native conversion where supported. Inspect
the resulting PPT, including text editability and all clipping boundaries.
Do not flatten the full figure into a slide image. Ordinary standalone
quantitative plots may keep their established SciencePlots/Matplotlib source;
do not replace measured curves with hand-drawn PowerPoint geometry.

The framework itself remains in native PPT, with one matching formal PDF and
PNG. SVG is an internal component format, not a separate authoring workflow.

## Inspect the composition, then the integrated page

For an aesthetic redraw, compare two small composition sketches before detailed
work. Select the one whose mechanism and contribution read most clearly at
publication size. After rendering, inspect hierarchy, alignment, stroke weight,
whitespace, proper math, connector routing, grayscale separation, and notation
against the paper. Shorten prose or enlarge/rearrange geometry when cramped;
never fix crowding by shrinking ordinary type below 8 pt.

For a figure worker, inspect only the assigned candidate against its brief and
return it with the source and direct evidence; do not modify the parent paper.
The main Engineer selects and embeds the final vector PDF, rebuilds changed
inputs, and inspects the inclusion at its actual size. Preserve an accepted
composition, scientific content, and numerical results. Complete the current
Engineer turn and return to the host, whose formal Reviewer judges the complete
paper. Do not dispatch an integrated/full-paper Reviewer, write the main
`paper/REVIEW.md`, or self-edit stage certificates. Technical validation alone
does not establish visual quality.
List individual final source/PDF/PNG paths in the handoff; keep obsolete
variants out of the formal filenames.
