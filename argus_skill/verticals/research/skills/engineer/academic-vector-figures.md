---
name: "Academic vector figures and mathematical typography"
description: "Compose precise paper diagrams with physical-unit Matplotlib, TikZ/LaTeX, or SVG, optionally combining them with PPT Master."
---

# Academic vector figures

Use after Figure Studio selects a composition. The active Engineer designs and
writes the figure; these are deterministic rendering tools, not another model
or review team. Do not change the project's configured model to use them.

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

## Matplotlib: math and custom geometry

The framework's optional research dependencies include Matplotlib. Run scripts
with the injected framework Python. The reusable canvas keeps physical units,
prose as SVG text, mathematical notation, embedded PDF fonts, and vector shapes:

```python
from argus_skill.verticals.research.academic_figure import (
    FigureCanvas, BLUE, LIGHT_BLUE,
)

fig = FigureCanvas(width=396, height=120)
fig.panel(10, 12, 'a', 'Exact verification')
fig.box(12, 40, 145, 42,
        'Candidate set\n' + r'$C_t=\{i:U_i\geq L_{q_t}\}$',
        fill=LIGHT_BLUE, edge=BLUE)
fig.arrow([(157, 61), (216, 61)])
fig.box(216, 40, 155, 42,
        'Winner separation\n' + r'$L_j>\max_{i\ne j}U_i$')
fig.export('paper/figures/method')
```

Coordinates are points measured from the upper-left corner. `text`, `line`,
`arrow`, and `box` are drawing primitives, not a template to fill with prose.
Use `fig.axes` for custom scientific geometry. Use mathtext for actual
subscripts, superscripts, Greek letters, sets, and operators. The bundled
DejaVu and STIX fonts avoid relying on platform-specific Arial glyph coverage.
The export validates label size, missing glyphs, clipping, and box padding
before replacing existing SVG/PDF/PNG files. It does not judge aesthetics,
connector semantics, or mathematical truth: inspect all three explicitly.

Keep the executable `.py` as canonical source. For metric panels, use the
existing SciencePlots workflow and real data, then compose at the same physical
font and stroke scale. Do not redraw a numerical plot as approximate shapes.

## TikZ/LaTeX: notation-heavy structures

Check `pdflatex` or `lualatex`, `tikz.sty`, and the chosen document class before
using them. A standalone TikZ source can typeset notation with the manuscript's
math conventions and export a vector PDF directly. Use an explicit bounding
box and physical units (`bp` for PDF points), set type at final size, and keep
the `.tex` source. Compile with `-halt-on-error -interaction=nonstopmode`; do
not enable shell escape. Render the result to PNG with PyMuPDF for inspection.
Do not reduce every label to ASCII to work around a broken conversion.

## SVG, layout tools, and mixed panels

SVG suits custom vector geometry; use local fonts/assets and inspect the actual
browser or PDF export. Matplotlib/LaTeX may supply mathematical labels as vector
glyphs while the editable program retains their source notation. Graphviz is
useful for topology coordinates when installed; restyle its output to the same
type and stroke scale. A default Graphviz or Mermaid theme is not a finished
academic design. A browser composition should use the existing local browser
renderer; an SVG-only exporter should not be forced onto a CSS layout.

Use PPT Master when native PowerPoint editing helps. Mathematical subpanels may
be vector shapes sourced from Matplotlib/LaTeX; avoid whole-slide rasterization.
The native PPTX must match the included PDF. If the chosen primary route is
Matplotlib, TikZ, or SVG and no PPTX was requested, keep its editable source
instead of manufacturing an inferior PowerPoint conversion.

## Inspect the composition, then the integrated page

For an aesthetic redraw, compare two small composition sketches before detailed
work. Select the one whose mechanism and contribution read most clearly at
publication size. After rendering, inspect hierarchy, alignment, stroke weight,
whitespace, proper math, connector routing, grayscale separation, and notation
against the paper. Shorten prose or enlarge/rearrange geometry when cramped;
never fix crowding by shrinking ordinary type below 8 pt.

Embed the final vector PDF, rebuild the paper, and inspect it at its actual
size. Preserve scientific content and numerical results. The independent
Reviewer makes the final judgment. Do not self-edit stage certificates or
declare a figure attractive merely because technical validation passed.
List individual final source/PDF/PNG paths in the handoff; keep obsolete
variants out of the formal filenames.
