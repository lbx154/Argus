---
name: method-figure
description: "Create, revise and audit top-conference teaser, framework, pipeline and module figures (YAML spec to SVG, PDF, PNG, editable PPTX and Visio VSDX); check assets/gallery/ before drawing."
---

# Conference figure making — router

The engine is deterministic: you write **what goes where in what order**, and
`scripts/cvprfig` computes geometry in final rendered points. Do not hand-write
SVG coordinates and do not draw with an image model — both produce the generic
look this skill exists to prevent.

Read fragments from disk as directed below. Do not work from memory of this file.

## 0. Is this the right skill?

| Request | Route |
|---|---|
| architecture / pipeline / framework / teaser / module / attention diagram | continue here |
| bar chart, curve, heatmap, scatter, ablation plot, radar | not this skill — matplotlib/seaborn |
| a graphical abstract for a *biomedical* journal | not this skill |
| "make my figure Visio-editable" | continue here, jump to step 5 |
| "draw the architecture from this code / config" | continue here, but start at step 3b |

## 1. Always load

1. [static/core/stance.md](static/core/stance.md) — the non-negotiables and the
   list of tells this skill exists to suppress.
2. [static/core/contract.md](static/core/contract.md) — the eight questions to
   answer before drawing, including the symbol table.

Then read [manifest.yaml](manifest.yaml) to see which further fragment the
current job needs.

## 2. Fill in the contract — do not skip

Answer, in your reply to the user, before writing any spec:

1. the one-sentence claim the figure must make;
2. the archetype;
3. which element is the contribution;
4. the semantic colour map;
5. venue and column width;
6. which real crops will fill the `image` slots;
7. the symbol table: every symbol the figure will show, its one-sentence
   meaning, and whether a label or the caption defines it — keep only symbols
   the text also uses, write the rest as words;
8. who will hand-edit the PPTX and which application exports the paper PDF
   (WPS, Keynote, LibreOffice, PowerPoint) — this sets `pptx_app`.

If the user gave a paper section or code, derive 1–4 from it and state your
reading back in two or three lines so it can be corrected cheaply. Ask a
question only when two readings would produce materially different figures.

## 3. Pick an archetype and start from its template

Load [references/archetypes.md](references/archetypes.md), then copy the
matching file out of `templates/` and edit it. Starting from a blank spec
wastes effort and loses the proportions that make the archetype work.

**Architecture papers**

| Archetype | Template |
|---|---|
| paradigm comparison, page 1 | `templates/teaser-comparison.yaml` |
| multi-stage pipeline | `templates/pipeline-4stage.yaml` |
| block zoom-in with tensor shapes | `templates/module-detail.yaml` |
| token / attention pattern | `templates/attention-tokens.yaml` |
| recurrent or streaming system | `templates/streaming-worldmodel.yaml` |
| teacher–student, contrastive, EMA | `templates/dual-branch.yaml` |
| adapter / gate / "we froze the rest" | `templates/gated-module.yaml` |
| real inputs left, prediction right | `templates/framework-with-io.yaml` |
| surround-camera / multi-view perception | `templates/surroundview-pipeline.yaml` |
| which parameters train, which are frozen | `templates/trainable-frozen.yaml` |
| distillation, teacher–student across modalities | `templates/teacher-student.yaml` |
| a pipeline too long for one row | `templates/wrapped-pipeline.yaml` |

**Analysis, evaluation and position papers** — the contribution is a
measurement or a distinction, not a network:

| Archetype | Template |
|---|---|
| what the standard metric cannot see, page 1 | `templates/blindspot-teaser.yaml` |
| the shape of an empirical study | `templates/study-overview.yaml` |
| a controlled 2 × 2 counterfactual or ablation | `templates/factorial-2x2.yaml` |
| where this work sits in the field | `templates/taxonomy.yaml` |

## 3b. Starting from code

When the user points at a model implementation, draft the spec from the source
rather than from a reading of it:

```bash
# a PyTorch package -- classes are found by parsing, never imported
python3 scripts/from_code.py path/to/models/ --list        # what is in there
python3 scripts/from_code.py path/to/models/ --model MyNet -o fig.yaml

# an mmengine / mmdet / mmdet3d config -- usually the better source, because
# it names every stage and its channel widths in pipeline order
python3 scripts/from_code.py configs/model.py --mm -o fig.yaml
```

Neither reader imports or executes the code. The output is a **first draft**
that gets the modules, their order and the branch structure right, and it will
have been reshaped to fit the column — the command prints what it dropped or
abbreviated to get there. Treat it as a starting spec, not an answer:

- rename boxes to the names the paper uses, not the attribute names;
- delete anything the paper does not discuss — the corpus median framework
  figure has 11 boxes, and a faithful 30-module dump is unreadable;
- attach real `src:` crops to the input and output slots;
- check the arrows against what `forward` actually does, especially where the
  reader reported a skipped branch.

Then continue at step 4.

## 4. Write the spec

Load [references/spec-language.md](references/spec-language.md) for the full
grammar. Load [references/house-style.md](references/house-style.md) when you
need a colour, a size or a stroke weight, and
[references/corpus-report.md](references/corpus-report.md) when the user asks
*why* something looks the way it does — every constant in the engine is a
measurement from 349 published figure PDFs and that document is the audit
trail, including the rules that were tested and cut. Load [references/case-studies.md](references/case-studies.md)
when the user names a paper whose figures they want to match.

Working rules:

- one `id` per element, named after the concept, not the position;
- `role:` before `fill:` — reach for an explicit colour only when no role fits;
- let the engine size boxes from their text; set `w`/`h` only for images,
  slabs and deliberate emphasis;
- put real content in the figure: 57% of framework diagrams in the corpus
  embed rasters, two thirds of them with inputs at the far left and
  predictions at the far right (`shape: image`, `imagegrid`, `cameraring`);
- `image` slots stay placeholders until the user supplies real crops — never
  invent an output render, and never pass off a crop from another paper as
  your own result;
- keep labels to noun phrases of six words or fewer;
- **write every variable as maths** — `"BEV features $z_{1:4}$"`,
  `"$\tilde c_s$"`, `"$\times L$"` — so it is italic exactly as in the
  text; fill in `symbols:` and `caption:` (or `caption_tex:
  sec/3_method.tex#fig:label`) from the contract, see
  [references/symbols-and-captions.md](references/symbols-and-captions.md);
- several figures in one paper share a `paper.yaml` (`paper: paper.yaml`)
  holding the role colours and the symbol table, so they cannot drift;
- line things up with `align_x: other` / `align_y: other`, not with
  hand-measured spacers;
- never use web-UI colours (Tailwind, Material, Bootstrap) — fills are pale
  Office tints, outlines black, saturated colour only on arrows and labels.

## 5. Render

```bash
python3 scripts/render.py spec.yaml -o out/overview -f svg,pdf,png,vsdx,pptx --dpi 600
```

`svg`, `vsdx` and `pptx` need no external tool. `pdf`, `png`, `tiff` and `emf`
use whichever of Inkscape, LibreOffice, rsvg-convert, cairosvg or ImageMagick
is installed; the command reports which one it used and skips cleanly if none
is. Rasters are taken from the PDF and the produced pixel size is checked
against `--dpi`, because some converters silently emit a 96 dpi screenshot.
Use 600 dpi for camera-ready, 1200 for a poster.

Read the report. It prints the canvas size, the node and edge counts, how many
PPTX arrows are glued connectors (and which stay freeform), and — if the
layout is wider than the column — the **effective point size** after scaling.
Body labels are 7.5 pt, stage titles 8.5 pt; under 6.0 pt is a failure,
6.0–6.5 pt a warning, and module names stay at 7.0 pt or more — the median of
the template papers users want to match, see
[references/corpus-report.md](references/corpus-report.md). To make
everything bigger, set `figure: {type_scale: 1.1}` and re-render; never
enlarge labels one by one in PowerPoint.

The PDF is written losslessly (LibreOffice with no downsampling) and audited
automatically when PyMuPDF is installed.

For editing by hand afterwards, load
[references/visio-workflow.md](references/visio-workflow.md). Generate the
drag-and-drop module palette once per project with:

```bash
python3 scripts/make_stencil.py -o templates/stencil -f vsdx,pptx
```

## 6. Audit — required before delivering

```bash
python3 scripts/validate.py spec.yaml --svg out/overview.svg
```

Fix every `FAIL`. Justify or fix every `WARN`. Beyond legibility and palette,
the auditor now checks that every symbol is defined (`undefined-symbol`,
`symbol-not-in-caption`), that the legend and the figure agree both ways
(`legend-unused-entry`, `unexplained-line-style`), that no bent arrow ends too
close to its last bend (`short-final-segment`), that wires do not share a
corridor, that no arrow runs through text, that embedded pictures carry 600
ppi at print size, and that no colour is a web-UI default (`ai-palette`).
Then do the eye pass in
[references/anti-ai-checklist.md](references/anti-ai-checklist.md): squint
test, trace one path, cover the caption, convert to greyscale.

**Look at the rendered figure.** Convert a page to PNG and actually view it —
the auditor catches geometry and palette, not whether the figure communicates.

## 7. Deliver

Load [references/latex-integration.md](references/latex-integration.md) and
hand back:

- the `\begin{figure*}` block with `width=\linewidth`;
- a caption that states the claim, names the parts, explains the colours and
  **defines every symbol the figure shows**, in reading order — then tell the
  user to recompile and check the page count;
- the spec file alongside the PDF so the figure can be regenerated;
- an explicit list of the `image` slots still holding placeholders;
- the hand-over rule, in one line: once they edit the PPTX, it is the only
  source — export the PDF from it with `export_pptx.py`, never re-render the
  spec over it.

## 8. After the user edits the PPTX — required

Whenever the user comes back with a hand-edited PPTX or a PDF exported from
one, audit the files that ship, not the spec. Load
[references/after-handover.md](references/after-handover.md).

```bash
python3 scripts/audit_pptx.py fig.pptx --width 496.8 --spec spec.yaml   # or --ref source.svg
python3 scripts/audit_pptx.py fig.pptx --width 496.8 --fix fig_fixed.pptx   # theme shadows, detached subscripts
python3 scripts/export_pptx.py fig.pptx --width 496.8                   # lossless, audits the PDF
python3 scripts/audit_pdf.py fig.pdf --width 496.8
```

`audit_pptx.py` works on any PPTX, including FigGenie's. Pass the width the
figure is included at. `--fix` writes a copy and leaves the user's file
alone. It switches off shadows inherited from the theme (LibreOffice draws
them, PowerPoint and Keynote hide them) and re-shifts subscripts that
LibreOffice would print detached. Hand the copy back, and say what changed.
A class legend for renders is checked against the pictures themselves. If
classes are reported missing, ask whether the full palette is kept on purpose
before deleting entries. Application differences (arrowhead sizes, Keynote's
whole-point type, LibreOffice's default 300 dpi JPEG export) are in
[references/app-compatibility.md](references/app-compatibility.md).

## Revising an existing figure

If the user has a figure they want matched or fixed:

1. read the source PDF's vector content to recover its real palette and sizes —
   `python3 - <<'EOF'` with `fitz` (PyMuPDF), as documented at the top of
   `references/house-style.md`;
2. write a spec that reproduces the structure;
3. change only what the user asked for;
4. audit and re-render.

Never silently restyle a figure the user is happy with.
