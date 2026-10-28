# Composing a conceptual paper figure

Use this for Figure 1 and every other conceptual, method, architecture,
taxonomy, teaser or graphical-abstract figure, while writing the paper and
when review asks for a figure repair. Read `RESEARCH_NOTES.md`, the current
manuscript, the executed method and the direct result sources first; a figure
that is not grounded in them is decoration. The outcome is one editable source
and the vector PDF and PNG exported from it. Tool choice does not establish
visual quality; a figure is judged by looking at the export at the width it
will be printed.

## Which route a figure takes

Quantitative charts are not this skill's subject. Every data, metric or result
chart, including uncertainty and ablation plots and the data panel of a mixed
figure, follows `data-figures.md`.

Conceptual figures are composed here, as native editable shapes, connectors
and text, in a tool that keeps them editable: PowerPoint or Keynote, draw.io,
Inkscape, or code that writes native shapes (python-pptx, for example). Keep
the editable source in `paper/figures/` (for example
`paper/figures/overview.pptx`), name it as the `source` in
`paper/figures/<stem>.source.json` (with any data files a data component reads
under `data`), export a vector PDF for the manuscript and a PNG at manuscript
width, and open the PNG before the figure goes in. An explicit operator choice
of tool overrides all of this.

What does not work: boxes made of matplotlib patches joined by `annotate`
arrows. Their labels overlap at publication size, they look unfinished, and
nobody can edit them afterwards. A layout tool such as Graphviz can compute
coordinates for an exact load-bearing topology, but the final nodes and
connectors are still drawn as editable shapes, because its default node-and-
arrow output is not a finished figure. A data chart inside a method figure is
drawn from actual data under the rules of `data-figures.md` and checked again
after it is placed.

Generated imagery is optional. When an authorized image generator is
available, it may supply a composition sketch, a background, a texture or a
non-semantic icon, nothing more: never a scientific label, number, arrow,
boundary or claim-bearing geometry, which are reconstructed from the
manuscript and the executed method and stay editable. Do not generate
quantitative plots. Write a minimal prompt grounded in the current paper that
forbids unsupported content, and do not send private manuscripts, code, data or
credentials without authorization. Inspect what comes back for accidental
text, watermarks, logos, misleading symbolism or content the paper does not
support.

## What Figure 1 must show

Every complete paper needs a real Figure 1 that shows the problem, the
mechanism and the claim-bearing flow at a glance. It enters the manuscript
through `\includegraphics` of the exported PDF, placed after the Introduction,
normally on page 2 or 3, and moving the float does not require redrawing; a
boxed paragraph or table inside a figure environment does not count.
Decorative depth (simulated 3D, shadows, ornaments) is a different thing from
the method's real architectural depth: keep the second, drop the first.
Topology fidelity takes priority over decorative richness. A polished Figure 1
does not need depth, icons or decorative complexity, and it never takes a
label, an arrow, a value or a branch condition from generated image text or
geometry.

## Making the figure

1. Reuse an existing suitable figure before creating another. A prose-only
   edit, a recompile or a new review round does not justify redrawing. An
   operator-rejected figure is not suitable for reuse merely because it
   compiles, has no overlaps or uses the suggested colours; redesign its
   composition before making local repairs.
2. Inspect published reference figures first: open the actual diagrams in two
   or three accepted papers from the selected venue or comparable conferences,
   chosen for their mechanism and composition rather than the paper's fame.
   Study reading order, the visual objects they use, information density, type,
   spacing and colour semantics without copying artwork, and recover how each
   exposes its mechanism through concrete objects and their relationships. A
   reference is useful only after its graphic has been inspected. Keep the
   paper URL, figure number and the useful observation in `RESEARCH_NOTES.md`,
   and reuse references already inspected for this paper when they still fit.
3. Sketch two or three genuinely different compositions (a generated sketch
   may be one of them) and choose by scientific reading order, clarity and
   economy at the actual paper width.
4. Build the chosen design as editable objects, restoring every scientific
   label, value, branch and arrow from the manuscript and the executed method.
5. Export, inspect the PNG at publication width, repair the editable source
   and export again. Compare it with the references: the mechanism should be
   apparent from the drawing, with a deliberate hierarchy and restrained,
   coherent colour. A grid of prose and formula boxes, or three boxes of bullet
   points, is a composition to redo, not a figure to include.

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
7. Build reusable groups for repeated tokens, fields, operators and labels,
   with shared sizes and styles and grouped by semantic role, so a change of
   notation or spacing stays easy to edit.
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
- Connectors terminate at explicit node boundaries; no shaft or arrowhead
  enters an unrelated node, label or panel. Route them through reserved
  corridors, dock them at the actual object boundary with one light stroke and
  arrowhead, let branches originate at one explicit decision rather than at
  nearby label text, keep return paths outside the forward flow, and fix the
  layout rather than the arrows when they would cross; connector penetration,
  overlap and clipping are defects.
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
  representation, not programming-style substitutes such as `C_t` or `J(pi)`
  where the paper uses mathematical notation.

### Visual craft

Aim for the precision of a strong design: consistent reusable components,
aligned edges and baselines, optical balance, and two or three levels of
hierarchy. Every stylistic choice is settled by one question: does it still
read at the printed width? Type is sized so that an ordinary label stays
readable when the figure is included at column width, which in practice is the
size of the paper's footnotes; anything smaller says the canvas holds too
much, and the remedy is to move content to the caption, not to shrink the
type. Panel headings need only a little more, never a slogan across the top.
Strokes are thin enough to read as drawing rather than frame at that width.
Colour is restrained: dark ink for reading, one or two semantic accents, pale
tints only where they delimit a group. Good defaults are ink `#28344A`, muted
labels `#69768A`, rules `#ACB6C4`, navy `#3C5488` for the shared mechanism and
teal `#008F7A` for the meaningful contrast, with tints `#EFF2F7` and
`#EEF6F3`; a muted terracotta `#C17664` may replace one accent when a contrast
is necessary. A paper that already has a coherent scientific palette keeps it;
either way, check greyscale and colour-vision separation. A coherent
sans-serif hierarchy for module names and annotations is fine even when the
body font is Times, and mathematics may use a compatible math face. Use a
small spacing unit at the final size, a wider gap between semantic groups than
within them, and give a long formula room rather than a smaller font.

One good existing figure needs no competition, and a larger batch of
candidates is worth its cost only when it answers an unresolved design
question. Reuse a good composition during local repairs, and do not ask the
operator to make routine layout decisions.

## From results to tables and figures

Analysis is the main work; figures follow from it. Compute every paper number
from raw rows; never hard-code an expected result. Before aggregating, match
the raw configuration and repeat identities and counts to the declared run,
account for failures and exclusions, and verify the claim-critical invariants:
a copied completion marker or a previously generated table cannot make a
partial or changed run complete, so the data, the code and configuration, and
the summaries of one validated attempt are kept together. Compare compatible
data, models, budgets, evaluators and uncertainty. Prefer a small
counterfactual regression when it directly tests whether a result or figure
changes under a claim-critical input change. Preserve valid losing rows in the
raw evidence while building the paper around the positive thesis. Where to
check is the Engineer's judgement of where the risk lies; whether the evidence
supports the claim is the Reviewer's to decide (`experiment-review.md`).

Use real measured values, correct units, conventional axes, readable labels
and uncertainty where it is scientifically relevant, and make the winning
comparison and takeaway immediately visible. A data refresh updates the
affected values from new raw rows without redesigning a selected composition.
Produce only analysis code, paper tables, editable figure sources and the
final exports that `paper/main.tex` uses.

## Keeping the science moving

Most effort belongs to the contribution, the methods, the decisive experiments
and their interpretation. When drawing needs more than a small local repair
and the science can continue independently, have the drawing done in parallel
(a subagent, or a separate figure task) and keep going. Give each candidate
drawer the scientific meaning, copied input excerpts and data, the source
locations, the final column width and the paper's palette, and its own
directory outside `paper/figures/`; a candidate drawer edits nothing else, and
checks only its own candidate for scientific fidelity, a readable render, and
an editable source with its export. If no parallel worker is available, draw
locally while experiments run.

The lead Engineer selects a completed, checked candidate as soon as one is
suitable, without waiting for optional alternatives, moves its source and
matching exports into `paper/figures/`, writes its `.source.json`, updates the
caption and manuscript, and checks them against the latest science. Keep one
editable source for every export; a delivered deck or drawing must depict the
same final composition as the PDF the paper includes.

After selection, inspect once at publication size, repair concrete problems
and keep the accepted composition. Reopen the design only for changed
scientific content, a specific fidelity or readability defect, or explicit
operator feedback, and update the smallest affected part; a new review round
or a cosmetic preference is not a reason. Do not create layout reports,
exemplar collections or visual-review files. The strict page-by-page visual
judgment is made once, by the Reviewer in the review step.
