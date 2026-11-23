# Five figures, taken apart

Deconstructions of the reference corpus. Read the one closest to your figure
before you write a spec. Colour values were read out of the published PDFs;
sizes are given at **final printed size** (native size × the include factor).

---

## SparseWorld — `overview.pdf` (arXiv 2510.17482)

Native 934 × 435 pt, included at `\linewidth` in a two-column AAAI paper
(504 pt), so the scale factor is 0.540.

**Structure.** Four stage columns — *Image Backbone*, *Range-Adaptive
Perception*, *State-Conditioned Forecasting*, *Parallel Decoders* — each a grey
dashed rounded container with a bold italic Times title above it.

**What makes it work:**

1. **Widths encode importance.** The two contribution stages are wide; the
   backbone and the decoders are narrow. A reader who looks for three seconds
   already knows where the paper's content is.
2. **Two nested tinted regions.** Inside *Perception*, the transformer block
   gets a `#FFF9E6`-ish wash with a `#BF9000` dashed border and a `×L` badge.
   Inside *Forecasting*, the whole stage gets a pale orange wash. The wash says
   "this repeats"; the badge says how often.
3. **Three arrow colours, each with a job.** Black for image/geometry flow,
   `#BF9000` olive for everything inside Perception, `#EA700D` orange for
   everything inside Forecasting. There is no legend, and none is needed.
4. **Grounded at both ends.** Six real camera crops on the left; a real
   occupancy render and a real planning raster on the right. The abstract part
   is sandwiched between two pieces of evidence.
5. **Queries drawn as extruded slabs**, coloured by what they are: pale blue
   for scene queries, lavender for the single ego query. The ego query is
   visually one slab against five — the asymmetry *is* the design.
6. **Residual connections as right-angle buses** on the right of the block,
   0.67 pt, same olive as the block.

**Reproduce with:** `templates/pipeline-4stage.yaml`.

---

## SparseWorld — `comparation.pdf`

Native 502 × 345 pt, single column (238 pt), factor 0.475. Labels land at
6.2 pt — the small end of what is readable, and a deliberate trade for fitting
three paradigms on page 1.

**Structure.** Three stacked rows, `(a)` `(b)` `(c) … (Ours)`, bold italic
titles left-aligned. A line-style key sits top-right in the band beside the
first title. A shared *Future Occupancy* column on the right spans rows inside
one grey dashed container.

**What makes it work:** every row is the same sentence — features → operator →
result. Only the operator and the representation change. The eye diffs three
rows instead of reading three diagrams. The `Perception` module is the same
green everywhere it appears.

**Reproduce with:** `templates/teaser-comparison.yaml`.

---

## GaussianWorld — `framework.pdf` (CVPR 2025, arXiv 2412.10373)

**The idiom worth stealing: the label *is* the legend.** "Historical
Gaussians" is set in dark red, and the arrow carrying historical Gaussians is
the same dark red. "Random Prior" is olive, and so is its arrow. "Current
Observation" is blue. Three data streams identified with zero legend area.

**Nesting.** Three levels — *3D Gaussian World Model* (solid navy border) ⊃
*Gaussian World Layer* (solid navy) ⊃ *Unified Refinement Block* (grey border,
grey wash). Titles sit *inside* the top of each container, centred, bold, not
italic. Depth 3 is the practical limit before the borders start to read as
noise.

**The time rail.** A single row of small squares along the bottom: filled for
history, empty for future, an ego-vehicle crop at `T`, `…` at both ends. It
costs 15 pt of height and it is the only thing in the figure that says
"streaming".

**Reproduce with:** `templates/streaming-worldmodel.yaml`.

---

## StreamVGGT — `framework.pdf` (arXiv 2507.11539)

The corpus's sans-serif member: `HelveticaNeue` labels, `Arial-BoldMT`
headings. Use `font: helvetica` when the venue's body font is sans.

**Tokens coloured by frame.** Each frame's tokens keep one colour all the way
across, so the reader can watch frame `T-2`'s tokens move through spatial and
then temporal attention. The camera token is gold in every frame — one
exception, consistently applied.

**Attention as a region, not a box.** *Spatial Attention* and *Temporal Causal
Attention* are tall pale bands with rotated labels and **no outline**. Tokens
pass *through* them. Drawing them as boxes would have said nothing; drawing
them as regions shows that they are operators over the whole token set.

**Causality made visible.** The temporal fan is triangular — later frames
receive from earlier ones and not the reverse. That triangle is the paper's
claim, drawn.

**The module name in red at the bottom** of the dashed container, rather than a
title above it: the container is inside a larger figure, so the name reads as a
caption for the region.

**Reproduce with:** `templates/attention-tokens.yaml`.

---

## EmbodiedOcc — `Main_new.pdf` (arXiv 2412.04380)

**Two-row temporal layout.** Frame `T-1` on top, frame `T` below, joined by
thick dark-red vertical arrows. Time runs *down*; computation runs *right*. Two
axes, unambiguous, no timeline needed.

**Chevrons for memory.** *Load Memory* and *Update Memory* are pink chevrons
pointing in the direction of the transfer. A box would have made them look like
computation; the chevron says "this is a move".

**Labels below, not inside.** *Predicted Depth Map*, *Multi-Scale Feature
Maps*, *Gaussian Vectors* are plain black Times set under the object. Inside
the box is for module names; under the box is for what the thing *is*. Use
`caption:` for the second kind.

**Feature maps as dashed-outline planes** and vectors as stacks of extruded
cubes — the reader can tell a tensor from a module at a glance because they are
different *kinds* of drawing, not different colours.

---

## TPVFormer — `framework.pdf` (CVPR 2023, arXiv 2302.07817)

The earliest figure in the corpus and the origin of much of the house style:
grey dashed stage containers, pale Office fills, black hairline outlines, real
camera crops on the left, a rendered result on the right.

Worth noting for what it *lacks*: no gradients, no shadows, no icons, no
rounded "card" aesthetic — three years before this was fashionable and three
years after. The style is stable because it is driven by print legibility, not
by trends.

---

## The pattern across all five

| | |
|---|---|
| Stages | 3–5, grey dashed containers, bold italic titles above |
| Fills | pale Office tints; grey for anything not claimed |
| Outlines | black, 0.65–0.81 pt |
| Arrow colour | black between stages, the stage's deep tint within |
| Labels | 6.2–7.6 pt, noun phrases, Times or Helvetica |
| Repetition | one block plus a `×N` badge, never unrolled |
| Tensors | extruded slabs, token grids, dashed planes, cubes |
| Encoders | trapezoids |
| Memory | chevrons |
| Evidence | real crops at the input and the output, always |
