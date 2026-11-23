# Changelog

## 3.1.0

Auditing all 17 figures of a real CVPR submission with 3.0 turned up three
defects in the auditors themselves: one problem they missed, one false
alarm, and one bad side effect of 3.0's own subscript fix. Each was measured
in LibreOffice and Keynote before it was changed.

### Fixed

- **Shadows inherited from the theme went unreported.** FigGenie's PPTX gives
  every shape an empty `<a:effectLst/>` plus `<p:style><a:effectRef idx="1|2">`,
  which points at theme effect styles that draw a 35–38 % `outerShdw`. By the
  OOXML spec the empty list cancels the shadow, and PowerPoint and Keynote
  draw none. LibreOffice ignores the override and draws all 139 shadows, then
  exports each one as a blurred 97–313 ppi grey picture. `audit_pptx.py` took
  the override at face value. It now resolves `effectRef` against the theme
  the slide master actually uses, and reports `raster-effect (theme)` as an
  error, whatever the shape writes.
- **Class legends for rendered images were reported as orphaned.** A legend
  for occupancy or segmentation classes (car, bus, vegetation …) explains
  colours that appear only inside embedded renders. Every qualitative figure
  of the submission got 10 `legend-orphan` errors. Both auditors now look
  inside the pictures: `audit_pptx.py` checks the embedded media,
  `validate.py` the files in image slots. A pixel counts for a legend colour
  when it is that colour times a shading factor between 0.45 and 1.2, so
  shaded voxels are matched. Calibration on Fig. 4's 24 renders: every legend
  class covers at least 0.2 % of some render, while clearly absent colours
  stay at or below 0.006 %. The threshold is 0.1 %. Grey and pale colours
  are never looked for, because backgrounds would match them. In a legend
  whose other entries are found, a class that occurs in no picture is
  reported once, as a warning. Keeping a full palette across figures is a
  legitimate choice. Needs Pillow or PyMuPDF; without either, the auditors
  say so rather than guess.
- **Enlarged subscripts dropped too far in LibreOffice.** LibreOffice prints a
  baseline-shifted run at 58.5 % of its size and moves it by baseline × the
  run's *own* size. So 3.0's 15 % larger scripts at −25 % sat 0.29 × body
  below the line. Scripts enlarged by hand to 1.25× sat 0.31 × body below,
  43 % of their own height, and a lone `d₁` looked like `d` and `1`. The
  writer now sets the shift in body terms and divides it by the enlargement:
  −17 % for subscripts (0.20 × body, the measured best being 7.8 pt on
  7.0 pt at −18 %), +28 % for superscripts (0.32 × body, as in the SVG).

### Added

- `audit_pptx.py --fix OUT.pptx` writes a repaired copy, then audits it.
  Theme effect references are set to 0 and the theme's effect styles are
  emptied. Subscripts LibreOffice would print detached are re-shifted.
  Every other byte is copied unchanged. On the submission's Fig. 2 it raised
  27 subscripts from −25 % to −16 %; on FigGenie's original it removed all
  139 shadows, and the LibreOffice PDF went from 157 images to 18.
- `script-too-low`: in `audit_pptx.py`, a subscript shifted more than about
  −20 %, which LibreOffice prints over 35 % of its own height below the line.
  In `audit_pdf.py`, the same drop measured in the PDF.
- `audit_pdf.py` `raster-shadow`: images that are one flat colour under a soft
  mask are reported together as rasterised shadows or transparency, instead of
  as one `image-low-ppi` per shadow plus a `page-is-raster`.
- `audit_pptx.py --legend-in-images` and the legend item key `in_image: true`
  declare that a colour is drawn inside the pictures.
- `audit_pdf.py --source fig.pptx`, passed automatically by `export_pptx.py`:
  a picture that was already JPEG in the PPTX is a note, not an `image-jpeg`
  warning. So is a JPEG at the native frame size of a common driving camera
  (nuScenes 1600×900, Waymo, KITTI, Argoverse 2, Cityscapes, DDAD, ONCE …).
  Camera photos are JPEG at the source.
- `audit_pptx.py` reads the theme through the slide → layout → master chain,
  not the first `theme*.xml` in the package.

## 3.0.0

Everything in this release comes from using 2.0 on a real CVPR submission.
The figure left the engine clean; the defects appeared afterwards, in the PPTX
a person edited in WPS and in the PDF exported from it. So the audit now
follows the files that ship, every symbol must be defined, type is larger, and
the PPTX writer was rebuilt to survive hand editing.

### Added — auditing what ships

- `scripts/audit_pptx.py` — audits any PPTX (this engine's, FigGenie's, or a
  hand-drawn one) at its printed width: `short-final-segment` (bent arrows WPS
  draws at 45°), `unglued-arrow`, `freeform-arrow`, `glue-broken`,
  `text-too-small`, `lost-subscript` (against `--spec` or a source `--ref`
  SVG, or heuristically: `z1:4`, `d1`), `legend-orphan`, `image-low-ppi`,
  `raster-effect`, `no-background`, `off-slide`, `mixed-fonts`, and the
  arrowhead size each application will draw. On the hand-edited overview of
  a real submission it reports exactly the 2 short final runs, 20 flattened
  subscripts and 1 orphaned legend entry that had reached the paper.
- `scripts/audit_pdf.py` — the PDF LaTeX includes: glyph sizes at the printed
  width, image ppi and JPEG/JPX encoding, unembedded and Type 3 fonts, pages
  that are one raster. Needs PyMuPDF.
- `scripts/export_pptx.py` — lossless PPTX → PDF through LibreOffice (no
  downsampling, lossless compression, quality 100) or Keynote (Best), followed
  by `audit_pdf`.
- `cvprfig/ooxml.py` — reads a PPTX back: absolute line paths through group
  transforms, flips and rotation; arrowheads; text runs with sizes and
  baselines; pictures with pixel and printed size; glue; effects.
- Rule, in `SKILL.md`, `stance.md` and `after-handover.md`: once the PPTX is
  edited it is the only source — export from it, never re-render over it.

### Added — symbols and captions

- `$...$` maths in every label: italic variables, upright digits and
  operators, TeX spacing around binary operators and relations, accents,
  `\mathrm`/`\mathbf`/`\mathbb`, Greek, primes, `\log`-style names; `\$` for
  a literal dollar.
- `symbols:` table and `caption:` / `caption_tex: file.tex#label`; checks
  `undefined-symbol`, `symbol-not-in-caption`, `symbol-label-bare`,
  `symbol-defined-elsewhere`, `upright-variable`, `symbol-style`,
  `unused-symbol`, `caption-long`. Operators (⊕ ⊗ ⓒ), stacking brackets and
  repeat badges (`×L`) count as symbols.
- `paper:` — one `paper.yaml` per paper holding venue, font, role colours and
  the symbol table.
- `contract.md` item 7, the symbol table; caption template gains a fourth
  sentence (define every symbol, in reading order) and a note to recompile and
  check the page count.
- `from_code.py` drafts declare the operators they draw and start a caption
  that defines them.

### Added — routing and the PPTX writer

- Glued connectors: `straightConnector1` / `bentConnector2` /
  `bentConnector3` with `stCxn`/`endCxn`, solved for rotation and flips; custom
  polygons carry connection sites where the arrows meet their outline.
  LibreOffice re-opens every template's PPTX with all 159 arrows where the
  engine put them (`tests/compat/lo_roundtrip.py`). Routes with three or more
  bends stay freeform and are listed.
- The run after the last bend is at least 2.5 arrowheads; when the geometry
  cannot allow it the auditor says so and the PPTX gives the head its own
  straight segment.
- Lanes: dog-legs that would jog on top of each other are spread 4 pt apart;
  edges sharing a port share one trunk; stage gaps widen until their wires fit
  (`corridor-too-narrow`, `edges-share-corridor`).
- Arrowhead presets chosen for the application that exports the PDF
  (`pptx_app: office | wps | keynote`) and drawn identically in the SVG;
  `pptx_heads: shape` for identical triangles everywhere;
  `pptx_connectors: freeform` for Keynote.
- The legend is emitted to PPTX and VSDX at all (it was missing), as one group
  with a sub-group per entry; legend items honour `dash:` and `arrow:`.
- Explicit white slide background (Keynote rendered our slides black).
- Script runs written 15 % larger to offset the applications' own reduction;
  whole-point type for `pptx_app: keynote`; slack on text boxes.
- `type_scale`, `align_x` / `align_y`, `label_rotate`, `fixed_gaps`.

### Added — auditor

`edge-crosses-text`, `diagonal-edge`, `label-overlap`, `text-off-canvas`,
`edge-off-canvas`, `legend-unused-entry`, `unexplained-line-style`,
`legend-overlap`, `ai-palette` (Tailwind / Material / Bootstrap stock
colours), `saturated-fill`, `tinted-outlines`, `arrow-rainbow`,
`image-low-ppi` / `image-soft` / `missing-image`, `module-label-small`,
`script-too-small`; legibility now covers every text element, not only module
labels.

### Changed — recalibrations

- **Type.** Body 7.0 → 7.5 pt, stage titles 7.8 → 8.5, captions 6.5 → 7.0,
  annotations 6.0 → 6.5. Floor 5.0 → 6.0 pt, warning 5.6 → 6.5 pt, module names
  ≥ 7.0 pt — measured on the template papers users hold up as the model
  (SparseWorld 7.5 pt median, Occ3D 8.2, BEVDepth 8.6); see
  `corpus-report.md`.
- **Roles.** `core`/`ours` gold → orange, `decoder` purple → green, `encoder`
  green → blue, `output` blue → green, `temporal` orange → gold, `aux` teal
  light → pale: blue in, green out, orange ours, grey inherited.
- **Strokes and heads.** `flow` 0.45 → 0.60 pt; arrowheads 3.97 × 3.97 pt
  (LibreOffice/PowerPoint `sm/sm`), `port_stub` 5 → 9.9 pt.
- SVG→PDF now prefers LibreOffice with lossless options; rsvg-convert
  mis-places the glyph after an italic "f" in Times New Roman on macOS.

### Fixed

- A caption wider than its shape stretched the shape and moved its ports to
  the caption's edge, so arrows started in empty space. Shapes keep their
  size; ports sit on the shape; a captioned row centres the shapes; the south
  port sits below the caption.
- Ports on slanted shapes (trapezoid, parallelogram, plane) now lie on the
  outline.
- A stage narrower than its title let the title spill off the canvas.
- Nested containers with a title above it did not reserve room for it.
- Arrows into a titled stage ran through the title.
- `anchor: start` labels were drawn from the box centre; notes ignored
  `anchor` in PPTX and VSDX.
- Leading blanks of a run were dropped by converters, collapsing math spacing.
- The VSDX writer printed inline markup literally (`*α*`, `**0.00**`) and
  emitted no legend.
- Edge labels in PPTX/VSDX were placed differently from the SVG.
- `e → e` routes ran through the target box.
- Facing ports a few points out of line produced 2 pt jogs; they are now one
  straight arrow.

### Templates

All sixteen templates and the quickstart were reworked for the new type and
roles, carry a symbol table and a claim-style caption, and audit with 0 errors
(the remaining warning is the empty image slots). Two had real defects the new
checks found: `teaser-comparison` listed two legend entries for lines it does
not draw; `gated-module` aligned gates with hand-measured spacers that broke
when the type grew (now `align_x`).


## 2.0.0

Re-derived the whole house style from a much larger corpus, and added a path
from source code to a figure.

### The corpus

46 papers with complete arXiv sources from Tsinghua MARS, Wenzhao Zheng's group
and Megvii: 292 figure environments, 298 tables, 349 figure PDFs read with
PyMuPDF. Full numbers and method in
[`references/corpus-report.md`](references/corpus-report.md).

### Added

- `scripts/from_code.py` — draft a spec from a PyTorch `nn.Module` source tree
  or an mmengine/mmdet `model = dict(...)` config. Neither reader imports or
  executes anything; both parse with `ast`. Recovers submodules, dataflow from
  `forward`, parallel branches, residual joins and `×N` repeats; prunes
  norm/dropout plumbing; infers roles from names; colours parallel branches
  apart when role inference finds nothing.
- A **column-fitting ladder** in the emitter: a draft wider than its column is
  reshaped — captions dropped, names abbreviated, peripheral boxes shed, and
  only as a last resort folded into two bands — instead of being silently
  downscaled into 5 pt type. Each concession is reported.
- `cvprfig/palettes.py` — the three measured colour systems (Office theme,
  diagrams.net stock pairs, matplotlib `tab10`) kept whole rather than blended.
- `cvprfig/imgsize.py` — PNG/JPEG/GIF/PDF dimensions with no dependencies, so
  an `image` node inherits the real aspect ratio of the file it points at.
- Shapes: `voxelgrid`, `planestack`, `gaussians`, `cameraring`, `imagegrid`,
  `lane`, and `marker` — the flame/snowflake trainable/frozen convention as
  **vector paths**, not pasted emoji.
- Templates: `framework-with-io`, `surroundview-pipeline`, `trainable-frozen`,
  `teacher-student`, `wrapped-pipeline`.
- Auditor check `no-real-content` / `empty-image-slot`: 57% of framework
  diagrams in the corpus embed rasters, two thirds with inputs at the far left
  and predictions at the far right.
- Backslash escaping in the inline markup, so `kernel\_size` prints literally.
  Generated labels use it; hand-written specs now can too.
- `Figure.fit_scale`, available after layout without rendering.
- `validate.audit()` accepts a spec dict as well as a path.

### Changed — recalibrations, with the measurement

- **Stroke weights were about 2× too heavy.** Rendered-width modes in the
  corpus are 0.19 / 0.36 / 0.45 / 0.80 pt. `STROKE` moved from
  0.30/0.65/0.80/1.10 to 0.19/0.28/0.36/0.45/0.80.
- **Type floor.** `MIN_RENDERED_PT` 5.5 → 5.0, with a new 5.6 pt warning tier.
  The corpus puts 29% of glyphs below 5.0 pt and its median per-figure smallest
  glyph at 5.4, so 5.5 as a hard floor rejected half the reference set.
- **Palette.** Families now come from `palettes.py`; `blue` is the Office Blue
  Accent 5 ladder (`#DEEBF7 …`), `navy` is Accent 1, and `steel`/`rose` are
  kept as aliases so v1 specs still load. Role defaults shifted toward the
  blue/green/orange triad, which carries 72% of coloured area in the corpus.
- `MAX_HUE_FAMILIES` 5 → 6 (corpus median is 5, 75th percentile 9).
- `role: core` now resolves to `gold.light` rather than `gold.soft`.

### Removed

- A **colour-balance check** that would have warned when most of a figure's
  filled area was saturated. The "84% of fill area is neutral" number behind it
  counted the page-sized white backdrop as a fill. Measured the way an auditor
  measures — coloured share of *box* fill area — the corpus median is 87%
  coloured, so the check would have fired on nearly every published figure. The
  reasoning is recorded under "Rules that were tested and cut" in the corpus
  report so it does not get reintroduced from intuition.


All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] — 2026-08-22

### Added
- Six archetypes, taking the catalogue from five to eleven. Four of them cover
  analysis, evaluation and position papers, which have no architecture to draw
  but do have an argument: `blindspot-teaser`, `study-overview`,
  `factorial-2x2`, `taxonomy`. Two more cover common architecture patterns:
  `dual-branch` (teacher–student, contrastive, EMA) and `gated-module`
  (adapters, LoRA, FiLM, calibration heads).
- Shapes `framestrip` (observation windows, masks, missingness topologies),
  `dist` (a density glyph that says "a distribution" without faking data), and
  a proper curly `brace`. All three are supported by the SVG, `.vsdx` and
  `.pptx` writers.
- TIFF export.
- `same_label_ok` on a node, to silence the one-concept-one-colour audit where
  two identically named things are deliberately contrasted.

### Fixed
- **High-resolution raster export.** `--dpi` was silently ignored: LibreOffice's
  PNG filter emits a 96 dpi screenshot whatever you ask for, so a 600 dpi
  request produced a 317 px image. Rasters are now always taken from the PDF
  with a rasteriser that honours a density flag, and `render.py` verifies the
  produced pixel count against `--dpi` and warns when a converter cuts the
  corner.
- Named stroke weights (`lw: emphasis`) crashed on nodes and frames; they
  worked only on edges.
- A `group` nested inside a `row: {gap: …, body: […]}` lost its children.
  Every container body now accepts both the plain-list and the inline-options
  form.
- The auditor treated bare text labels as obstacles when checking whether an
  edge crosses a module, producing false positives for lines passing between
  two centred labels.

## [1.0.0] — 2026-08-22

### Added
- Initial release: the layout engine, the measured house style, five
  archetypes, the SVG / PDF / PNG / EMF / `.vsdx` / `.pptx` writers, the
  auditor, the Visio and PowerPoint module stencil, and the reference set.
