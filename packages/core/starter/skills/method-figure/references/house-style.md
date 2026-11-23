# House style, and where every number came from

This is not a taste document. Every constant in `scripts/cvprfig/style.py` and
`palettes.py` was measured out of the vector content of published figures with
PyMuPDF — fill colours, stroke widths, font names and glyph sizes read
directly from the PDFs, scaled by the `\includegraphics` factor that puts them
on the page. The full audit trail is [corpus-report.md](corpus-report.md).

## The single most useful finding

**These figures are PowerPoint, Visio and draw.io files.** The fills are
exactly the Microsoft Office theme tint ladder; the font tables still carry
`SimSun` and `Calibri` next to `TimesNewRomanPSMT`. If you want output that
reads as drawn rather than generated, use the colours the authors actually
clicked. A "nicer" palette reads as foreign immediately — and the "nicer"
palettes are exactly the web-UI colours image models default to.

## Typography (v3)

v2.0 calibrated type on the whole 46-paper corpus and set labels at 7.0 pt.
Figures built that way printed at 5.8–7.3 pt, and readers found them small.
v3 recalibrated on the papers users hold up as the target, measured the same
way:

| Figure | Tool | Printed glyph size |
|---|---|---|
| SparseWorld overview | Visio | p10 6.4, **median 7.5** |
| SparseWorld comparison | Visio | p10 6.1, median 6.6, p90 7.5 |
| Occ3D pipeline | PowerPoint | median 8.2 |
| BEVDepth backbone | Keynote | median 8.6 |
| OccFusion teaser | PowerPoint | median 6.4 |

| Tier | Size | Style |
|---|---|---|
| stage title | 8.5 pt | bold italic serif |
| panel title | 8.5 pt | bold |
| module label | **7.5 pt** | regular (bold for `strong`) |
| maths | 7.5 pt | italic variables, upright operators |
| caption under a shape, edge label, legend | 7.0 pt | regular |
| annotation, badge (`×L`) | 6.5 pt | italic |
| floor | **6.0 pt** error, **6.5 pt** warning, module names ≥ **7.0 pt** | |

Sub/superscripts are 0.72 × their base in the SVG. Need everything bigger?
`figure: {type_scale: 1.1}` scales every size in the spec and re-lays out the
figure; do not enlarge labels one by one in PowerPoint.

Serif (Times) is the default because Times/Nimbus/CMR carry 71 % of
characters in the corpus diagrams. Match the paper's body font.

## Colour

Families come from the Office ladder (`palettes.OFFICE`), rungs named
`pale light soft mid strong deep dark`:

```
blue    #EAF1FA #DEEBF7 #BDD7EE #9DC3E6 #5B9BD5 #2E75B6 #1F4E79
green   #EFF6E9 #E2F0D9 #C5E0B4 #A9D18E #70AD47 #548235 #375623
orange  #FDF2E9 #FBE5D6 #F8CBAD #F4B183 #ED7D31 #C55A11 #843C0C
gold    #FFF9E6 #FFF2CC #FFE699 #FFD966 #FFC000 #BF9000 #997300
grey    #F2F2F2 #E7E6E6 #D9D9D9 #BFBFBF #A6A6A6 #7F7F7F #595959
navy, red, purple, teal: same ladder, see palettes.py
```

Default roles — blue in and encoded, green out, warm orange for the paper's
own module, grey for everything inherited. This is the convention the
template papers share, and the blue/green/orange triad carries 72 % of the
coloured area in the corpus:

| Role | Fill | Use |
|---|---|---|
| `input`, `baseline` | grey.light | raw inputs, inherited parts |
| `backbone` | navy.light | pretrained feature extractors |
| `encoder` | blue.light | encoding |
| `attention` | blue.soft | token mixing |
| `core`, `ours` | **orange.light** | the contribution (was gold in v2.0) |
| `temporal` | gold.light | anything time-indexed |
| `decoder` | **green.light** | heads (was purple in v2.0) |
| `output` | green.soft | predictions |
| `memory` | red.light | state, caches |
| `loss` | red.soft | objectives |
| `frozen` / `trainable` | blue.pale / orange.pale | parameter status |
| `aux` | teal.pale | auxiliary branches |
| `neutral` | grey.pale | everything else |

Override the map once per paper in `paper.yaml` (`roles: {core: green.light}`)
so every figure agrees.

Rules the auditor enforces:

- **Fills are pale.** A module filled with a saturated colour (saturation
  > 0.45 and lightness < 0.72) is `saturated-fill`.
- **Outlines are black.** More than half the modules outlined in their own
  hue is the UI-kit card look (`tinted-outlines`).
- **Saturated colour lives on arrows and label text** that must be traced back
  to an entity, and on small marks (tokens, frames).
- **No web-UI defaults.** Tailwind, Material and Bootstrap swatches
  (`#3B82F6`, `#6366F1`, `#8B5CF6`, `#10B981`, `#2196F3`, `#0D6EFD`, …) are an
  error (`ai-palette`): an author clicking through PowerPoint never lands on
  them, image models and generated plotting code always do.
- **Few arrow hues.** More than three saturated arrow hues is `arrow-rainbow`.
- **Every meaningful line style is explained**: a dashed or coloured arrow is
  in the legend, labelled, or coloured after the module, stage or label it
  belongs to; every legend entry is used.

## Strokes and arrowheads

| Weight | pt | Use |
|---|---|---|
| hairline | 0.19 | image outlines, cell grids |
| thin | 0.28 | |
| box / frame | 0.36 | module and stage outlines (corpus mode) |
| flow | **0.60** | arrows (0.45 in v2.0) |
| emphasis | 0.80 | the contribution's outline |

Arrowheads are sized for the application that produces the paper PDF (see
[app-compatibility.md](app-compatibility.md)): 3.97 × 3.97 pt for a thin
line exported by LibreOffice or PowerPoint. The SVG draws exactly the head the
PPTX asks for. A bent arrow's last run is at least 2.5 heads (~10 pt).

## Geometry

| Property | Value |
|---|---|
| horizontal text padding | 7.0 pt (SparseWorld: box − text = 14 pt) |
| vertical text padding | 4.2 pt |
| module corner radius | 2.6 pt |
| stage container | grey dashed, 3.2/2.2, radius 5 |
| sibling gaps | 16 pt across, 9 pt down |
| lane between parallel wires | 4 pt |

## Recurring idioms

- **Stage columns.** Grey dashed rounded container per phase, bold italic
  title above it.
- **Tinted region** for the repeated block: pale wash, dashed border in the
  deep tint of the same hue, `×L` badge top right.
- **Colour-matched flow.** Arrows inside a stage take its deep tint; arrows
  between stages are black. Label text set in an entity's colour is the
  legend, done inline.
- **Extruded slabs** for token banks; **trapezoids** for encoders/decoders;
  **chevrons** for memory reads/writes.
- **Real crops** of inputs and outputs at both ends.

## What the corpus never does

No gradients, shadows, glow or bevels. No icons of gears, brains or clouds.
No left colour strips on cards, no curved Bézier arrows coloured by source,
no rounded sans-serif. No more than two type families. No colour that is not a
pale Office tint, a deep Office tint on an arrow or a label, or black.
