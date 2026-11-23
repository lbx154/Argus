# The spec language

A figure is a YAML (or JSON) document. The author states *what goes where in
what order*; the engine decides pixels. Load it with
`python3 scripts/render.py spec.yaml -o out/name -f svg,pdf,vsdx,pptx`.

```yaml
figure:   # canvas
layout:   # how the top-level panels are arranged
panels:   # the panels themselves, each holding a layout tree
edges:    # arrows, by node id
notes:    # free-floating annotations
legend:   # an optional key
```

## `figure`

| Key | Default | Meaning |
|---|---|---|
| `id` | `figure` | output stem, and the Visio page title |
| `venue` | `cvpr` | `cvpr` `iccv` `eccv` `neurips` `iclr` `icml` `aaai` `acl` `siggraph` `generic` |
| `width` | `double` | `single`, `double`, or a number in points |
| `width_frac` | `1.0` | for `\includegraphics[width=0.8\linewidth]` |
| `font` | `times` | `times` or `helvetica`; match the venue body font |
| `pad` | `2.0` | canvas margin, points |
| `pad_top` / `pad_bottom` | `0` | extra room, e.g. for a key beside the first title |
| `bg` | white | canvas colour |
| `height` | auto | force a minimum canvas height |
| `type_scale` | `1.0` | multiply every font size, then re-lay out (change size here, not in PowerPoint) |
| `pptx_app` | `office` | which application exports the paper PDF: `office` (LibreOffice/PowerPoint), `wps`, `keynote`; sets arrowhead presets (and whole-point type for Keynote) |
| `pptx_heads` | `native` | `shape` draws PPTX arrowheads as triangles, identical in every application |
| `pptx_connectors` | `native` | `freeform` writes plain paths instead of glued connectors (for Keynote) |

Top-level keys besides `figure`:

| Key | Meaning |
|---|---|
| `paper` | path to a shared `paper.yaml` with `venue`, `font`, `roles`, `symbols` |
| `roles` | override the role → colour map, e.g. `{core: green.light}` |
| `symbols` | the symbol table, see [symbols-and-captions.md](symbols-and-captions.md) |
| `caption` | the figure caption (LaTeX), checked against `symbols` |
| `caption_tex` | `path/to/section.tex#fig:label`, read the caption out of the paper |
| `fixed_gaps` | `true` stops the engine widening gaps for the wires that jog in them |

## `layout` and `panels`

`layout` is `row` (default), `col` or `grid` (with `cols: N`). Each panel is a
container:

| Key | Meaning |
|---|---|
| `id` | referenced by edges; a container is a valid edge endpoint |
| `title` | stage title |
| `title_pos` | `above` (default), `below`, `inside-top`, `inside-bottom` |
| `title_align` | `center` (default), `left`, `right` |
| `title_size` / `title_color` / `title_bold` / `title_italic` | overrides |
| `frame` | `none` (default), `dashed`, `solid`, `region` |
| `frame_color`, `frame_lw`, `corner` | frame styling |
| `fill` | a wash behind the whole stage |
| `badge` | small italic mark in the top-right, e.g. `"$\\times L$"` (a symbol: define *L*) |
| `pad`, `padt`, `padr`, `padb`, `padl` | inner padding |
| `gap` | space between children |
| `align` | cross-axis: `center` (default), `start`, `end` |
| `justify` | main-axis distribution |
| `valign` | how this panel sits in the top-level row: `top` (default), `middle`, `bottom` |
| `body` / `row` / `col` / `stack` | the children |

### The layout tree

Inside `body`, an item is one of:

```yaml
- {id: a, text: "Feature Sampling", role: core}     # a node
- row: [ ... ]                                       # a horizontal run
- row: {gap: 8, align: end, body: [ ... ]}           # ...with options
- col: [ ... ]                                       # a vertical stack
- group: {id: blk, fill: orange.pale, frame: region,   # a tinted sub-region
          badge: "$\\times L$", body: [ ... ]}
- group: {id: outer, frame: region,                  # groups nest, and a
          row: [ {group: {id: inner, body: [...]}},  # nested group is the
                 {id: x, text: "…"} ]}               # cleanest way to draw
                                                     # a subset relation
```

Every container body accepts either a plain list or the
`{gap: …, align: …, body: [...]}` form, in `row`, `col`, `group` and `body`
alike.

**Uniform snapping.** Box-shaped siblings in a `col` are snapped to the widest,
and in a `row` to the tallest. This is what makes a stack of modules look
deliberate. Containers are *not* snapped. Opt out per node with `snap: false`.

## Nodes

Common keys, all optional except `id`:

| Key | Meaning |
|---|---|
| `shape` | see the vocabulary below; default `box` |
| `text` | the label; `\n` breaks lines |
| `role` | semantic role, drives fill (see below) |
| `tint` | `pale` `light` `soft` `mid` `strong` `deep` `dark` within the role's family |
| `fill`, `stroke`, `text_color` | explicit overrides |
| `outline` | `ink` (default, black), `match` (the role's deep tint), `none` |
| `lw` | outline weight in points, or a name: `hairline` `box` `flow` `emphasis` |
| `dash` | dashed outline |
| `w`, `h`, `minw`, `minh` | force geometry; otherwise sized from the text |
| `wrap` | wrap the label to this width in points |
| `size`, `bold`, `italic` | type overrides |
| `rotate` | `90` for vertical text in a tall thin box |
| `caption` | text hung below the shape, with `caption_size`, `caption_color` |
| `badge` | corner mark, with `badge_pos`: `ne` `nw` `se` `sw` `n` |
| `at: [x, y]` | pin to absolute canvas coordinates, escaping the flow |
| `same_label_ok` | silence the one-concept-one-colour audit for this node; use only when two identically named things are deliberately contrasted |
| `dx`, `dy` | nudge without leaving the flow |

### Inline markup in any label

`*italic*`, `**bold**`, `Q^t`, `Q_t`, `Q^{t+1}`, `Q_{i,j}`, `\` escapes.

**Write variables as maths:** `"BEV features $z_{1:4}$"`, `"$\tilde c_s$"`,
`"$N \times C$"`, `"$\times L$"`. Inside `$...$` letters are italic, digits
and operators upright, binary operators spaced as in TeX, accents compose.
The supported commands and the symbol table are in
[symbols-and-captions.md](symbols-and-captions.md).

### Shape vocabulary

| `shape` | Use for | Notable keys |
|---|---|---|
| `box` | a module (default) | |
| `sharpbox` | a frame, a timeline cell | |
| `trapezoid` | an encoder | `dir`: `right` `left` `up` `down`, `slant` |
| `invtrapezoid` | a decoder | `dir` |
| `chevron` | a memory read/write | `dir`, `point` |
| `hexagon` | a gate or a switch | |
| `parallelogram` | data in flight | `skew` |
| `plane` | a ground plane / BEV | `skew` |
| `slab` | a query or token bank | `n`, `cell`, `cellh`, `cellgap`, `colors` |
| `cube` | a dense voxel volume | `side`, `grid` |
| `tokengrid` | a token sequence | `rows`, `cols`, `cell`, `cellgap`, `colors` |
| `circleop` | ⊕ ⊗ ⊙ | `op`: `+` `x` `c` or any glyph, `d` |
| `image` | a real crop | `src` (relative to the spec), `w`, `h`, `fit` |
| `framestrip` | an observation window, a mask, a missingness pattern | `pattern` (`"..####.."`), `mask` (`[0,1,…]`), `cell`, `cellgap`, `on_fill`, `off_fill` |
| `dist` | "a predictive distribution", without faking data | `kind`: `gauss` `laplace` `bimodal`, `spread`, `skew` |
| `brace` | a grouping brace | `dir`: `right` `left` `down` `up`, `depth` |
| `imagestack` | a stack of frames | `n`, `offset` |
| `text` | a bare label | |
| `ellipsis` | "…" | `dir: down` for vertical |
| `spacer` | measured whitespace | `w`, `h` |
| `brace` | a grouping brace | `dir` |

`image` without `src` renders a labelled placeholder — deliberately obvious, so
an unfinished figure cannot be mistaken for a finished one.

### Semantic roles

`input` `backbone` `encoder` `attention` `core` `temporal` `memory` `decoder`
`output` `aux` `baseline` `ours` `loss` `neutral`

Colour can also be written directly as `#RRGGBB`, `family.tint`
(`orange.soft`), a bare family (`blue`) or a role name. In a *stroke* or *arrow*
position, a bare family or role resolves to its saturated end; in a *fill*
position, to its pale end. That is deliberate and matches how the corpus works.

## `edges`

```yaml
edges:
  - {from: fs, to: am}                       # ports chosen automatically
  - {from: mhsa.e, to: an1.e, route: bus, bend: 9, color: orange.deep}
  - {from: occ.e, to: q0.w, route: hv, label: "features", lw: box}
```

| Key | Default | Meaning |
|---|---|---|
| `from`, `to` | — | `id` or `id.side`; side ∈ `n s e w c nw ne sw se` |
| `route` | `auto` | `auto` `straight` `hv` `vh` `bus` |
| `via` | — | explicit `[[x, y], ...]` waypoints |
| `mid` | `0.5` | where the dog-leg turns, as a fraction |
| `stub` | `9.9` | straight run out of the port before the first turn |
| `bend` | `12` | how far a `bus` route stands off |
| `color` | black | any colour expression; resolves to the saturated end |
| `lw` | `flow` | `hairline` `box` `flow` `emphasis`, or a number |
| `dash` | off | `true`, or `flow` `thin` `region` |
| `arrow` | `end` | `end` `start` `both` `none` |
| `head` | `tri` | `tri` (filled) or `open` |
| `label`, `label_size`, `label_dx`, `label_dy`, `label_pos`, `label_anchor` | | |
| `label_rotate` | `0` | `-90`/`90` sets the label along a vertical wire |

Whatever the route, the path always leaves along the source port's normal and
arrives along the target's. Containers are valid endpoints, so
`{from: block.s, to: occ.n}` connects a whole stage; into a stage whose title
sits above it, the arrow stops at the title instead of running through it.

What the router guarantees, without being asked:

- the run after the last bend of an arrow is at least 2.5 arrowheads (~10 pt),
  so no application mis-aims the head; when the geometry cannot allow it the
  auditor says so (`short-final-segment`) and the PPTX gives the head its own
  straight segment;
- dog-legs that would jog on top of each other get separate lanes, 4 pt
  apart; edges sharing a port share one trunk;
- the gap between two stages is widened until every wire jogging in it fits
  (switch off with `fixed_gaps: true`);
- facing ports a few points out of line get one straight arrow, not a 2 pt
  jog;
- if only one end names its side (`to: o_d.w`), the other end leaves facing
  it;
- arrows meet slanted outlines (trapezoid, parallelogram, plane) on the
  outline, and a captioned shape's south port sits below its caption.

## `notes`

```yaml
notes:
  - {text: "$Q^t$", near: qbank.ne, dx: 3, dy: -4}
  - {text: "regime A  →  B", near: inp.nw, dy: -6.5, anchor: start}
  - {text: "Ego Pose", at: [210, 96], color: "#953735", italic: false}
```

## `legend`

```yaml
legend:
  at: [140, 3]        # or corner: ne | nw | se | sw
  dir: col            # or row
  box: false
  items:
    - {text: "gradient detached", color: "#C00000", kind: dashed}
    - {text: "differ in one factor", kind: dashed, dash: thin, arrow: false, lw: hairline}
    - {text: "learnable", color: orange.soft, kind: swatch}
```

`kind` is `line`, `arrow`, `dashed` or `swatch`; `arrow:` and `dash:` override.
The auditor fails an entry that nothing in the figure uses
(`legend-unused-entry`) and warns about a dashed or coloured line style that
is in no legend and carries no label. In the PPTX the legend is one group.

A class legend for renders (occupancy, segmentation) names colours that only
the pictures in `image` slots use. The auditor looks for them there,
counting shaded tones, with Pillow or PyMuPDF. Mark an entry
`in_image: true` to declare it without looking:

```yaml
legend:
  items:
    - {text: car, kind: swatch, color: "#FF9E00"}
    - {text: pedestrian, kind: swatch, color: "#0000E6", in_image: true}
```

## Placing things by hand

The flow layout covers most cases, but two escape hatches exist and both are
used by the shipped templates:

- **alignment** — `align_x: other` places a node's centre directly under or
  over `other`'s after layout, `align_y: other` level with it. This is how
  `gated-module.yaml` puts each gate under the operator it drives, and unlike
  a hand-measured spacer it survives any change of type size;
- **measured whitespace** — a `spacer` with an explicit `w`/`h` inside a `row`
  with `justify: start` positions the next item exactly;
- **`at: [x, y]`** — absolute canvas coordinates, for the rare annotation that
  belongs nowhere in the flow.

To find the number to put in a spacer, ask the engine:

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, "scripts")
from cvprfig import Figure, load_path
f = Figure(load_path("spec.yaml"), base=".")
for k in ("op_s", "gate1"):
    n = f.nodes[k]; print(k, "x=%.2f cx=%.2f w=%.2f" % (n.x, n.cx, n.w))
EOF
```

## A minimal complete spec

```yaml
figure: {id: demo, venue: cvpr, width: single, font: times}
panels:
  - id: s1
    title: "Encoder"
    frame: dashed
    body:
      - {id: img, shape: image, w: 56, h: 38, caption: "Input"}
      - {id: enc, shape: trapezoid, text: "Backbone", role: encoder}
  - id: s2
    title: "Ours"
    frame: dashed
    body:
      - {id: q, shape: slab, n: 4, cell: 7, cellh: 24, role: attention}
      - {id: blk, text: "Sparse Refinement", role: core}
edges:
  - {from: img, to: enc}
  - {from: enc.e, to: q.w}
  - {from: q, to: blk}
```
