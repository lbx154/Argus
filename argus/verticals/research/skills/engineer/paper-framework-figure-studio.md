---
name: "Composing a conceptual paper figure"
description: "Draw Figure 1 and every other conceptual, method, architecture, taxonomy or teaser figure as native editable PowerPoint objects through PPT Master: Method D (reference figures, an image design blueprint, an editable reconstruction) is the default and Method B (direct native design without an image API) is the fallback. Also says which figures are data charts for the shared SciencePlots/Matplotlib helper instead, what generated imagery may and may not supply, how results become tables and figures, and how drawing is kept from stalling the science."
---

# Composing a conceptual paper figure

Use this in Paper, and during figure repairs in Review, for Figure 1 and any
other conceptual, method, architecture, taxonomy, teaser or graphical-abstract
figure. Read the research notes in `RESEARCH_NOTES.md`, the current manuscript,
the executed method and the direct result sources first; a figure that is not
grounded in them is decoration. The outcome is one canonical editable source,
`paper/figures/<name>.pptx`, and the vector PDF and PNG exported from it. Tool
choice does not establish visual quality; a figure is judged by looking at the
export at the width it will be printed.

## Which route a figure takes

Quantitative charts are not this skill's subject. Any paper data/metric/result
chart, including uncertainty and ablation plots and the data panel of a mixed
figure, stay on the SciencePlots/Matplotlib route: the analysis script passes
series and per-seed rows to the `paper_charts` helper described in
`engineer/paper-chart-styling.md`, the single SciencePlots/Matplotlib
data-figure path of the paper; that skill's ECharts route
(`figure_spec_scripts/echarts_figure.py`, browser-rendered vector) is the one
alternative for a standalone chart.

Conceptual figures are composed here. Method D is the default; Method B is the
fallback. Both D and B author the framework in native editable PowerPoint
objects: the difference is only whether an image-generated design blueprint
informs the composition. Either way the paper receives editable native PPTX
through PPT Master and the vector PDF exported from it. An explicit operator
choice overrides the default.

Some tools supply parts of a figure without being a route of their own.
FigureSpec (`engineer/figure_spec_scripts/figure_renderer.py`, whose docstring
explains its input) or Graphviz can compute coordinates for an exact
load-bearing topology; the final nodes and connectors are still drawn as native
PPT objects, because their default node-and-arrow output is not a finished
framework figure. ECharts can supply a data chart component inside a method
figure, drawn from actual data with animation disabled, fixed dimensions and
the SVG renderer, and checked again after conversion into the PPT.
`engineer/research_visual_scripts/browser_render.py` renders such an HTML or
SVG component to a vector file; a browser-rendered text-card layout is not a
third conceptual route. Generated imagery (image-2), when configured, provides
a design blueprint or a non-claim-bearing illustrative asset, nothing more.

What is not a route: matplotlib `FancyBboxPatch` boxes joined by `annotate`
arrows (nobody can edit them, their labels overlap at publication size,
`figure_lint` reports the export and the Reviewer returns it), TeX-compiled
drawings, and hand-written SVG. There is no separate SVG workflow. SVG remains
an internal format of a renderer, and PPT Master's own SVG conversion is part
of the export, but it is never the source the paper keeps.

## What Figure 1 must show

Every complete paper needs a real Figure 1 that shows the problem, the
mechanism and the claim-bearing flow at a glance. It enters the manuscript
through `\includegraphics` of the exported PDF, placed after the Introduction,
normally on page 2 or 3, and moving the float does not require redrawing; a boxed
paragraph or table inside a figure environment does not count. Decorative
depth (simulated 3D, shadows, ornaments) is a different thing from the
method's real architectural depth: keep the second, drop the first. Topology
fidelity takes priority over decorative richness. A polished Figure 1 does not
need depth, icons or decorative complexity, and it never takes a label, an
arrow, a value or a branch condition from generated image text or geometry.

## Method D: reference figures, a blueprint, an editable reconstruction

**Method D is the default: reference figures, an image-API design blueprint,
an editable reconstruction, native PPTX through PPT Master, paper export.**

1. Reuse an existing suitable figure or blueprint before creating another. A
   prose-only edit, a recompile or a new Review round does not justify
   regeneration. An operator-rejected figure is not suitable for reuse merely
   because it compiles, has no overlaps or uses the suggested colours; redesign
   its composition before making local repairs.
2. Inspect suitable published reference figures before generating a new
   blueprint: open the actual diagrams in two or three accepted papers from the
   selected venue or comparable conferences, chosen for their mechanism and
   composition rather than the paper's fame. Study reading order, the visual
   objects they use, information density, type, spacing and colour semantics
   without copying artwork, and recover how each exposes its mechanism through
   concrete objects and their relationships. A reference is useful only after
   its graphic has been inspected. Keep the paper URL, figure number and the
   useful observation in the research notes, not in an exemplar collection, and
   reuse references already inspected for this paper when they still fit.
3. Check the configured image route, disclosure authorization, the remaining
   budget and the installed PPT Master. This default does not authorize
   spending beyond the task budget, uploading confidential material, changing
   providers or installing tools; existing operator authorization applies and
   is not requested again. If a prerequisite is unavailable, or the task's
   privacy, time or output constraints rule it out, use Method B and state the
   concrete reason in the research notes or the task response. If the operator
   explicitly requires Method D only, or an output format the fallback cannot
   deliver, surface the blocker instead of silently substituting.
4. Generate one visual design blueprint from a minimal, disclosure-safe prompt
   (see Generated imagery). At most one initial request per design direction:
   cheap layout sketches settle the alternatives first, and text or geometry
   fixes are repaired locally rather than by calling the API again. Preserve
   the actual returned image and prompt beside the drawing source, without
   credentials. A failed request is not a blueprint.
5. Reconstruct the design as editable objects, restoring every scientific
   label, value, branch and arrow from the manuscript and the executed method,
   never from the generated image's text or geometry. Follow an explicit model
   choice when one is given; Method D does not require a particular
   reconstruction model. Inspect the actual image rather than claiming that a
   local drawing used an API.
6. Export native editable PPTX objects with the installed toolkit (below). Do
   not paste the blueprint as a whole-slide raster and call it editable. Keep
   the upstream-required source files and include the vector PDF in the paper.
7. Inspect the render at the actual publication width, repair the editable
   source and render again. Compare it with the references: the mechanism
   should be apparent from the drawing, with a deliberate hierarchy and
   restrained, coherent colour. A grid of prose and formula boxes is not a
   finished illustration.

## Method B: direct native PPT design

**Method B is the fallback: direct native PPT design without an image API.** An
unavailable image interface selects B on its own; it is not a reason to pause
the paper or to ask the operator to configure an API. Study the references as
in D, design the composition, and create native editable shapes, connectors
and text through the installed PPT Master. Keep the PPTX and its generation
source together with the matching vector PDF and PNG. Method B does not
require image-generation credentials and holds the same scientific fidelity
and visual standard as D. Never relabel a Method B drawing as an API-assisted
reconstruction; name the workflow actually used.

## Generated imagery

Image generation is optional infrastructure; the paper can proceed without it.
When an authorized route is configured (model API status reports it), use it
for a visual design blueprint, a background, a texture or a non-semantic icon.
A blueprint is a composition reference, never a source of scientific labels,
numbers, arrows, boundaries or claim-bearing geometry; those are reconstructed
from authoritative sources and stay editable. Do not generate quantitative
result plots.

Write a minimal prompt grounded in the current paper that forbids unsupported
content, carry the visual craft below into it (a white background, dark ink
with one or two accents, thin strokes, deliberate whitespace, clear scientific
grouping), and leave long explanation for the caption. Do not send private
manuscripts, code, data or credentials without authorization. Generate one
candidate with `python -m argus.tools.image_api generate`, then inspect it for
accidental text, watermarks, logos, misleading symbolism or content the paper
does not support. Keep the image and prompt with the figure source; for a
decorative asset place only its useful non-semantic portion and keep the prompt
only if the asset may need regenerating. Do not create registration files or
separate visual-review reports.

## The PPT Master toolkit

The global skill `engineer/presentation-master.md` holds the toolkit notes:
locating the pinned checkout (`python -m argus.tools.ppt_master status`),
running its scripts through the supplied interpreter, keeping objects native,
and the font and line-break details of SVG-to-PPTX conversion. Read it before
the first figure. Keep figure projects and outputs in the authorized workdir,
outside the installed toolkit.

The canonical source is the native PPTX at `paper/figures/<name>.pptx`; the
included `<name>.pdf` keeps the same stem so the host can pair them. The
export needs neither PowerPoint nor LibreOffice:

```bash
EXPORT=$(find "$ARGUS_SKILL_HOME" . -name pptx_export.py -path '*figure_spec_scripts*' 2>/dev/null | head -1)
"${ARGUS_SKILL_PYTHON:-python3}" "$EXPORT" --pptx paper/figures/<name>.pptx   # writes <name>.pdf and <name>.png
```

It reads the PPTX with `pptx_to_svg.py`, renders the slide in the browser,
keeps the slide SVG under `paper/figures/src/<name>/` and records provenance.
The PDF's producer then says Skia/PDF; a PDF beside a PPTX with any other
producer (pdfTeX, Ghostscript, cairo, matplotlib) is reported by `figure_lint`
as not exported from the PPTX. Open `<name>.png` after every export: that is
the figure at manuscript width, and three boxes of bullet points in it are a
composition to redo, not a figure to include.

For a browser-rendered component inside the chosen route, keep assets local,
disable animation, fix the dimensions, and render the existing SVG or a PDF:

```bash
RENDER=$(find "$ARGUS_SKILL_HOME" . -name browser_render.py \
  -path '*research_visual_scripts*' 2>/dev/null | head -1)
"${ARGUS_SKILL_PYTHON:-python3}" "$RENDER" \
  --input paper/figures/src/<id>/index.html \
  --selector '[data-figure-root]' \
  --output paper/figures/<id>.pdf \
  --width 1200 --height 720
```

An SVG output requires an SVG in the page; a CSS composition should ask for PDF
rather than trigger `figure root contains no SVG`.

Text and objects in the PPTX must stay editable, and if the PDF and PNG
previews come from SVG rather than a PowerPoint render, say so rather than
claiming an Office rendering was inspected.

## Composition

Strong published figures reuse a small set of compositions. Pick the one that
fits the paper's actual claim before drawing anything; none is the default for
every paper, and the exemplars name structures rather than replacing the
inspection of figures from the paper's own area.

| Archetype | Use when | Structure | Exemplars |
|---|---|---|---|
| Pipeline strip | The contribution has a traceable forward transformation | A shared visual spine carries concrete inputs and intermediate representations through the real operations; expose the contribution inside its group and distinguish genuine training or feedback paths | RAG, InstructGPT, DreamFusion |
| Contrast diptych | The contribution is best stated as a delta against a standard approach | Two panels, old left and new right, drawn as the same diagram differing in exactly one visible attribute (a deleted box, a changed loss, one added matrix); the method panel may get more area | DPO, Chain-of-Thought, ReAct |
| Lineage progression | The contribution generalizes a known paradigm | Three lettered panels: two familiar paradigms, then the contribution in the terminal position; panel letters cited from the body text | VAR |
| Overview plus zoom | The novelty lives inside one block of an otherwise standard pipeline | Panel (a): the full pipeline at cartoon level showing where the block sits; panel (b): the single novel unit magnified with its internal wiring and dimensions | Stable Diffusion 3, NSA |
| Results-first teaser | The strongest claim is empirical | Figure 1 carries no architecture: a sample grid, a filmstrip contrast, or one headline plot with a bold takeaway sentence opening the caption; the mechanism moves to Figure 2 | VAR, Genie, Rho-1 |
| Coverage map | Benchmark, dataset, or evaluation papers | A colour-coded taxonomy tree, spectrum bar, or specimen grid whose legend marks which parts are new; the caption carries most of the explanation | DecodingTrust, Aya |

Then design from the science:

1. State the figure's one-sentence takeaway and reduce the claim to one
   visible transformation or comparison before adding labels. Reuse the same
   visual object on both sides so a reader sees what changed: the same event
   stream above aligned codewords for a coding paper, the candidate set
   shrinking while the selected token stays fixed for sparse inference. Derive
   any concrete example from the method and mark schematic quantities as such.
2. List the exact modules, labels and connections, including each connection's
   source, target, direction, boundary port and meaning, and match every
   visible name, direction and value to the paper and the executed method
   verbatim. Update affected notation in the source and the export without
   redesigning an accepted composition.
3. Give information a shape. A sequence is a row of tokens, a code is aligned
   bit fields, a set is a cluster or row of candidates, an interval is a span
   with endpoints, and a module box is for a real module. Richness comes from
   these relationships: do not turn every sentence or formula into another
   card, and do not invent modules, sampled values or toy results to fill the
   canvas.
4. Make the contribution unmistakable through subtraction or one minimal
   difference wherever possible (delete a box the baseline needs, mark the
   inherited parts frozen, change one token), so the baseline diagram is one
   visual edit away from yours. When subtraction is impossible, choose one
   primary emphasis: terminal panel position, one reserved accent against a
   muted base, an ours-versus-existing legend, or extra area. Render inherited
   machinery quietly while preserving its meaningful structure.
5. Lay out a quiet backbone and place the contribution where the eye should
   land. Show the load-bearing internal operations, interfaces and feedback (a
   module name alone does not explain a contribution) and let the mechanism
   set the number of panels. Avoid both a wall of prose boxes and an empty
   input-model-output strip that hides the contribution: if the figure feels
   sparse because the mechanism is missing, add the supported mechanism; if
   its content is complete, tighten the canvas; if it feels crowded,
   reorganize the groups or move explanation to the caption. Where it helps
   comprehension, run one concrete input and its intermediate representations
   through the diagram.
6. Keep colour semantic: one colour means one concept, identically in every
   panel and matched to the results charts. If a legend line cannot say what a
   colour means, remove the colour. Reserve one accent for the contribution or
   the selected path and keep inherited machinery quiet.
7. Build reusable native PPT groups for repeated tokens, fields, operators and
   labels, with shared sizes and styles and grouped by semantic role, so a
   change of notation or spacing stays easy to edit.
8. Write the caption to stand alone: open with the takeaway (bold when the
   venue style allows), walk the panels in reading order, decode every colour,
   symbol and badge, and name the contrast explicitly. Reuse panel letters and
   stage numbers as anchors in the body text. Never caption a figure "System
   architecture", and keep supporting derivations and narrative in the caption
   rather than on the canvas.

### Geometry

Set the canvas to the final single- or double-column width before drawing, and
export vector; a figure drawn at another size and scaled in LaTeX has type of
the wrong size no matter how careful the drawing was.

- A clear reading order: a dominant left-to-right flow for a real pipeline,
  parallel analyses kept parallel rather than given an invented serial
  dependency, and return or training arrows visibly different (dashed or a
  distinct colour).
- Connectors are drawn so that connectors terminate at explicit node
  boundaries; no shaft or arrowhead enters an unrelated node, label or panel.
  Route them through reserved corridors, dock them at the actual object
  boundary with one light stroke and arrowhead, let branches originate at one
  explicit decision rather than at nearby label text, keep return paths
  outside the forward flow, and fix the layout rather than the arrows when
  they would cross; connector penetration, overlap and clipping are defects.
- One shape class per concept, used identically everywhere; every element in
  one step persists visibly into the next, or its removal is the labelled
  action.
- Annotate real dimensions where they matter and mark arbitrary counts with an
  ellipsis or a multiplier, so drawn counts are never read as exact.
- Every visual property either encodes a declared meaning or stays neutral: no
  decorative 3D, gradients, shadows, backgrounds, icons or rounded cards that
  carry no information.
- Gloss any named component a general reviewer may not know; no bare acronym
  in a box.
- Real subscripts, superscripts, set notation, Greek letters and operators use
  native equation objects or properly positioned text runs with compatible
  math fonts. A failed font or conversion calls for a different math
  representation or renderer, not programming-style substitutes such as `C_t`
  or `J(pi)` where the paper uses mathematical notation.

### Visual craft

Aim for the precision of a strong design: consistent reusable components,
aligned edges and baselines, optical balance, and two or three levels of
hierarchy. Every stylistic choice is settled by one question: does it still
read at the printed width? Type is sized so that an ordinary label stays
readable when the figure is included at column width, which in practice is
the size of the paper's footnotes; anything smaller says the canvas holds too
much, and the remedy is to move content to the caption, not to shrink the
type. Panel headings need only a little more, never a slogan across the top.
Strokes are thin enough to read as drawing rather than frame at that width.
Colour is restrained: dark ink for reading, one or two semantic accents, pale
tints only where they delimit a group. The studio's defaults are ink `#28344A`,
muted labels `#69768A`, rules `#ACB6C4`, navy `#3C5488` for the shared
mechanism and teal `#008F7A` for the meaningful contrast, with tints `#EFF2F7`
and `#EEF6F3`; a muted terracotta `#C17664` may replace one accent when a
contrast is necessary. A paper that already has a coherent scientific palette
keeps it; either way, check grayscale and colour-vision separation. A coherent
sans-serif hierarchy for module names and annotations is fine even when the
body font is Times, and mathematics may use a compatible math face. Use a
small spacing unit at the final size, a wider gap between semantic groups than
within them, and give a long formula room rather than a smaller font.

For a new or unsuccessful figure, sketch two or three genuinely different
compositions before detailed rendering and choose by scientific reading order,
clarity and economy at the actual paper width. One good existing figure needs
no competition, and a larger batch is worth its cost only when it answers an
unresolved design question. Reuse a good composition during local repairs, and
do not ask the operator to make routine layout decisions.

## From results to tables and figures

Analysis is the main work; figures follow from it. Compute every paper number
from raw rows; never hard-code an expected result. Before aggregating, match
the raw configuration and repeat identities and counts to the declared run,
account for failures and exclusions, and verify the claim-critical
invariants: a copied completion marker or a previously generated table cannot
make a partial or changed run complete, so the data, the code and
configuration, and the summaries of one validated attempt are kept together.
Compare compatible data, models, budgets, evaluators and uncertainty. Prefer a
small counterfactual regression when it directly tests whether a result or
figure changes under a claim-critical input change. Preserve valid losing rows
in the raw evidence while building the paper around the positive thesis that
meets the Paper entry bar. Where to check is the Engineer's judgement of where
the risk lies; whether the evidence supports the claim is a question the
Reviewer decides (`reviewer/experiment-results-review.md`).

Use real measured values, correct units, conventional axes, readable labels
and uncertainty where it is scientifically relevant, and make the winning
comparison and takeaway immediately visible. A data refresh updates the
affected values from new raw rows without redesigning a selected composition.
Produce only analysis code, paper tables, editable figure sources and the
final exports that `paper/main.tex` uses; the main Engineer embeds the selected
claim-bearing tables and figures there.

## Keeping the science moving

Most effort belongs to the contribution, the methods, the decisive experiments
and their interpretation. When drawing needs more than a small local repair
and scientific work can continue independently, delegate the drawing in
parallel and keep going. A provider's native subtask can draw one candidate in
a private working directory, or return a full design and editable source
inline when the delegate is read-only, in which case the lead persists it (a
path-only acknowledgement is not a result). For independent Engineer-Reviewer
work, prepare one figure-only Team task per candidate as `agent-team-lead.md`
in the global library describes, each with its own working directory and local
task state and `owns_paths` naming only its output directory, registered with
the running project so its Curator can execute them. Give each worker the
scientific meaning, copied input excerpts and data, the authoritative source
locations, the final column width and the paper's palette; keep candidates
under an internal directory such as
`.argus/figure_candidates/<figure-id>/<batch-id>/<candidate-id>/`, and forbid
edits to the parent manuscript, evidence, figures, research notes and
`paper/REVIEW.md`. An ordinary brief in that directory is enough; do not
create another project-visible planning or review document, and do not
fabricate a beauty score for a leaderboard. If independent task state cannot
be prepared, use the native subtask route or draw locally while scientific jobs
run.

A candidate worker checks only its own candidate against the brief, for
scientific fidelity, a readable render, and editable source and final included
export; it never judges the whole paper, merges itself or starts more teams.
The lead selects a completed, locally checked candidate as soon as one is
suitable, without waiting for optional alternatives, promotes its source and
matching exports to `paper/figures/`, updates the caption and manuscript, and
checks them against the latest science; the remaining workers keep their
private directories and never gain write authority over the selected source.
Keep one canonical source for every formal export. If a PPTX is also
delivered, it must depict the same final composition; an old deck must not be
presented as the source of a new PDF. Name each final source and export with
its full path so it is openable.

After selection, inspect once at publication size, repair concrete problems
and retain the accepted composition; record the chosen paths and any
remaining material defect briefly in the existing checkpoint. Reopen the
design only for changed scientific content, a specific fidelity or
readability defect, or explicit operator feedback, and update the smallest
affected part. A new review round or a cosmetic preference is not a reason.

Paper is responsible for complete figures and a successful compile, not a
separate visual check. Do not create layout reports, exemplar collections,
provenance records or visual-review
files. The strict page-by-page visual judgment is made once, in Review, by the
host's Reviewer after the main Engineer returns. The Engineer does not
dispatch an integrated or full-paper Reviewer, start a second paper-wide
review loop, or write the main `paper/REVIEW.md`; a worker's local approval
does not complete the paper, and pending optional alternatives do not hold the
main turn.
