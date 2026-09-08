---
name: "Composing a conceptual paper figure"
description: "Default to Method D with Method B native PPT fallback; compose precise, informative academic figures with Figma-level visual care, meaningful mechanism detail, and optional ECharts components."
---

# Composing a conceptual paper figure

Use this in Paper for Figure 1 or another conceptual, method, architecture, or
taxonomy figure. Read the research notes in `RESEARCH_NOTES.md`, the current manuscript, the executed method,
and direct result sources. Create the canonical editable figure source and the
matching vector PDF and PNG. Method D is the default and Method B is the
fallback, as defined below. Use precise mathematical/vector components inside
the selected reconstruction when useful. Tool choice does not establish visual
quality; inspect the actual figure and its publication-size inclusion.

## Keep science moving while figures are drawn

Most effort belongs to the contribution, methods, decisive experiments, and
interpretation. Plan three informative figures for a full paper, with at least
two distinct scientific figures: usually the mechanism, the main comparison,
and an ablation, diagnostic, or generalization result. Each answers a different
question from real evidence. Do not pad the count with decorative images,
duplicate plots, or separately numbered pieces of the same diagram. A request
for only one figure or a partial paper keeps its requested scope.

When drawing needs more than a small local repair and scientific work can
continue independently, delegate it in parallel. The lead Engineer supplies
the scientific meaning, direct source material, the final column width, and
the paper's palette; then continues methods, experiments, or analysis. Start
with two or three genuinely different compositions, not a dozen polished
versions. One good existing figure needs no competition. A larger batch, even
twelve candidates, is an option only when it answers an unresolved design
question and resources permit; it is never a quota. Stop expanding the search
as soon as a scientifically faithful, refined candidate is good enough to use.
Keep provider and compute capacity available for the scientific work.

Use only a delegation interface actually available in the current session:

- A provider's native subtask tool can draw one candidate in a private working
  directory, or return a full design and editable source inline if the delegate
  is read-only. Do not ask a read-only delegate to save files or accept a
  path-only acknowledgement as its result. State the exact allowed output
  directory and forbid edits to the parent manuscript, evidence, figures,
  research notes, and `paper/REVIEW.md`. The lead persists an inline result.
- For independent Engineer–Reviewer work, follow `agent-team-lead.md` and use
  `python -m argus_skill.tools.team`. Manager/Planner prepares each candidate
  as a direct, figure-only task with its own working directory and local task
  state. Do not copy the parent paper's pipeline or venue state into it. An
  empty directory alone is not an initialized direct task. Candidate Reviewer
  judges only its figure; the paper's integrated Reviewer owns venue acceptance.
  Each task's `cwd` points to its independent candidate tree and `owns_paths`
  names only that tree's output directory. Keep the campaign registered with
  the original running project so its resident Curator can execute the tasks.
  `owns_paths` is a coordination rule, not a filesystem sandbox. If independent
  task state cannot be prepared, use the available native subtask route or do
  the small figure task locally while scientific jobs run.

Keep candidates under an internal directory such as
`.argus/figure_candidates/<figure-id>/<batch-id>/<candidate-id>/`. Give each
worker its own copied input excerpts and data, plus the authoritative source
locations for checking; do not share mutable PPT projects or output paths.
An ordinary brief in that directory is enough. Do not create another
project-visible planning or review document.

For the Team route, write the prepared tasks to a private `tasks.jsonl` using
the real fields `task_id`, `objective`, `acceptance_check`, `cwd`, and
`owns_paths`. The objective names the input brief, a single candidate output,
the actual PPT/plot render command, and the permitted writes. For example, a
candidate with its own `cwd` owns `output/**`; its acceptance check requires a
faithful, readable render and matching editable source. The runtime writes
the result shard; do not fabricate a numerical beauty score for its leaderboard.
With those tasks and paths prepared, the lead uses the existing control CLI:

```bash
"${ARGUS_SKILL_PYTHON:-python3}" -m argus_skill.tools.team form \
  --root "$ARGUS_FIGURE_TEAM_ROOT" --team-id "$ARGUS_FIGURE_TEAM_ID" \
  --cwd "$ARGUS_FIGURE_PARENT_WORKDIR" \
  --mission "Draw the specified figure candidates while the lead advances the science" \
  --tasks "$ARGUS_FIGURE_TASKS_FILE"
"${ARGUS_SKILL_PYTHON:-python3}" -m argus_skill.tools.team pool-set \
  --root "$ARGUS_FIGURE_TEAM_ROOT" --width 2 --state running
```

The Curator launches and reaps teammates. Continue scientific work and inspect
`team status --root ...` at useful handoff points instead of polling. Once a
suitable candidate is available, set `pool-set --state draining` to stop new
starts, let running work settle, and `dissolve` the team. Only the lead Engineer
chooses and promotes the final source and matching exports to `paper/figures/`,
updates the caption/manuscript, and checks them against the latest science.
Candidate workers never merge themselves or start more teams. Their reviews
check only the candidate's scientific fidelity, readable render, and editable
source against the supplied brief. They do not judge the whole paper or its
venue recommendation. The main Engineer finishes this round's scientific
repairs, integrates the chosen figure when ready, recompiles changed inputs,
and returns the current artifacts and evidence to the host. The host invokes
the formal Reviewer on the complete current paper. Do not spawn a native agent
or Team to act as an integrated/full-paper Reviewer, start another paper-wide
review loop, or write the main `paper/REVIEW.md`. The existing host review may
request another revision; a worker's local figure approval does not complete
the paper. Pending optional design alternatives do not hold the main turn.

After selection, inspect once at publication size, repair concrete problems,
and retain the accepted composition. Record the chosen source/export paths and
any remaining material defect briefly in the existing checkpoint. A new review
round is not a reason to reopen the design search. Reopen only for changed
scientific content, a specific fidelity/readability defect, or explicit operator
feedback; update the smallest affected part. A data refresh does not require a
new framework layout. Optional cosmetic preferences do not delay scientific
review or an otherwise acceptable paper.

## Default Method D; fallback Method B

This is the routing contract for conceptual, method, architecture, taxonomy,
teaser, and graphical-abstract figures. Quantitative charts, including the data
panel of a mixed figure, stay on the SciencePlots/Matplotlib route.
An explicit operator choice overrides the default.

**Method D is the default: reference figures -> image-API design blueprint ->
editable reconstruction -> native PPTX through PPT Master -> paper export.**

Both D and B author the framework in native editable PowerPoint objects.
An unavailable image interface selects B automatically; it is not a reason
to pause the paper or ask the operator to configure an API.

1. Reuse an existing suitable figure or blueprint before creating another.
   A prose-only edit, compile, or new Review round does not justify regeneration.
   Ground labels and connections in the current manuscript and executed method.
   Inspect suitable published reference figures before generating a new
   blueprint; open the actual diagrams in two or three relevant accepted papers
   from the selected venue or comparable conferences. Choose references for
   the mechanism and composition, not just a famous paper's name. Study reading
   order, visual representations, information density, type, spacing, and color
   semantics without copying artwork. Recover how the reference exposes its
   mechanism through concrete objects and internal relationships; a reference
   is useful only after its actual graphic has been inspected. Reuse references
   already inspected for this paper when they still fit. Keep the paper URL, figure number, and
   the useful design observation in the existing research notes, not an
   exemplar collection. An operator-rejected figure is not suitable for reuse
   merely because it compiles, has no overlaps, or uses the suggested colors.
   Redesign its composition before making local repairs.
2. Check the configured image route, disclosure authorization, available budget,
   and installed PPT Master via `engineer/presentation-master.md`. This default
   does not authorize spending beyond the task budget, uploading confidential
   material, changing providers, or installing tools. Existing operator authorization applies; do not request it again. If a prerequisite is
   unavailable or the task's privacy, time, or output constraints rule it out,
   use Method B and state the concrete reason in the existing research notes
   or task response. If the operator explicitly requires Method D only or an
   output format the fallback cannot deliver, surface the blocker instead of
   silently substituting another method.
3. Use `paper-illustration-image2.md` to generate a visual design blueprint from
   a minimal disclosure-safe prompt. Do not upload a whole private manuscript,
   raw experiment data, credentials, or code. Use at most one initial API
   blueprint per selected design direction; cheap layout sketches can establish
   the alternatives first. Reuse it and repair locally rather than repeatedly
   calling the API for text or geometry fixes. Preserve the actual returned image and prompt alongside
   the drawing source, without credentials. A failed request is not a blueprint.
4. The active Engineer or assigned figure worker reconstructs the design as editable objects, restoring
   every scientific label, value, branch, and arrow from authoritative sources,
   not from generated image text or geometry. Follow an explicit model choice;
   Method D does not require a particular reconstruction model. Inspect the
   actual image rather than claiming that a local drawing used an API.
5. Follow `engineer/presentation-master.md` and its installed upstream workflow
   to export native editable PPTX objects. Do not paste the blueprint as a
   whole-slide raster and call it editable. Retain the upstream-required source
   and export files, and include a publication-ready vector PDF in the paper.
6. Inspect the rendered figure at the actual publication width, repair the
   editable source, and rerender. Check native PPTX text and object editability.
   Compare it visually with the references: the mechanism must be apparent
   from the drawing, with deliberate hierarchy and restrained, coherent color.
   A grid of prose/formula boxes is not a finished academic illustration.
   Use small token streams, bit fields, sets, matrices, trajectories, or other
   scientifically meaningful objects where they reveal what changes and what
   stays fixed. Keep only essential equations on canvas and move derivations
   and narrative into the caption. Make this comparison during the existing
   render inspection; do not create another report or review stage.
   If PDF/PNG previews come from SVG rather than a PowerPoint render, say so;
   do not claim an Office rendering was inspected when it was not.

**Method B is the fallback: direct native PPT design without an image API.**
The Engineer or assigned figure worker studies the references, designs the composition, and uses
the installed PPT Master to create native editable shapes, connectors, and
text. Follow `engineer/presentation-master.md` for the actual toolkit route.
Keep the PPTX and its canonical generation source, plus the matching vector
PDF and PNG. SVG may be an internal source/export format, but there is no
separate SVG workflow or routing entry. Method B does not require image-generation
credentials and preserves the same scientific fidelity and visual standard as D.
Never relabel a Method B drawing as an API-assisted reconstruction.

ECharts may supply a genuine data chart within the PPT composition. Read the
actual data, disable animation, set final dimensions and type scale, and use
the SVG renderer for a vector component. Keep its option/data source and check
its conversion into the final PPT. ECharts is not a substitute for composing
the method with editable PowerPoint objects. Ordinary standalone result plots
can retain the established SciencePlots/Matplotlib route.

## Publication style

Apply these defaults to a new figure unless the paper already has an established
style. Aim for a carefully composed academic illustration with the precision of
a strong Figma design: consistent reusable components, clear alignment, optical
balance, and deliberate information hierarchy. This is a visual standard within
the D/B native PPT workflow, not a requirement to add another design tool.

The overview should explain the scientific idea at a glance and reveal useful
mechanism detail on a closer look at the same publication size. Give a reader
real objects to follow: aligned token or codeword ribbons, candidate sets,
intervals and thresholds, tensor blocks, page/graph neighborhoods, meaningful
branches, or before/after states, as the actual method warrants. Richness comes
from those relationships. Do not invent modules, sampled values, or toy results
to make the canvas look busy.

Avoid both a wall of prose boxes and an empty input–model–output strip that hides
the contribution. Expose the key transformation inside its group, keep labels
beside the objects they explain, and use whitespace to separate relationships.
If the figure feels sparse because the mechanism is missing, add the supported
mechanism; if its content is complete, tighten the canvas instead of decorating
it. If it feels crowded, reorganize groups or move explanation to the caption
while retaining the scientific structure and readable type. Judge this once
with the candidate inspection; do not introduce a density score or a new gate.

- Start with the scientific reading order, two or three levels of visual
  hierarchy, and meaningful phase containers. Show tokens, candidate sets,
  matrices or operators where they explain the mechanism. Keep prose in the
  caption instead of adding a full-width paragraph inside the figure.
- Use a white canvas, charcoal text, 0.5–0.9 pt strokes at publication size,
  and one or two restrained semantic accents. Pale fill belongs only where it
  helps group the mechanism. Align edges and baselines, allow visible internal
  padding, and leave connector corridors open. Do not give every step a large
  colored card, heavy rounded border, or pill badge.
- Default to a restrained scientific palette: ink `#28344A`, muted labels
  `#69768A`, rules `#ACB6C4`, navy `#3C5488`, and teal `#008F7A`. Use pale
  tints `#EFF2F7` and `#EEF6F3` only for the selected semantic groups. A muted
  terracotta `#C17664` may replace one accent for a necessary contrast; do not
  accumulate all colors. Avoid a separate peach/yellow/green/purple fill for
  every module. Preserve a paper's existing coherent scientific palette when
  it is already stronger, and check grayscale and color-vision separation.
- Use a coherent sans-serif hierarchy for module names and annotations;
  mathematical notation may use a compatible math face. A manuscript's Times
  body font does not require every diagram label to use Times New Roman.
- Size ordinary labels for the actual included paper width (normally 8–9 pt,
  never below 8 pt). Panel headings generally need only 9–10 pt; avoid a large
  slogan across the top. Use short panel letters where useful. Let content set
  geometry and move prose into the caption instead of reducing type.
- Render real subscripts, superscripts, set notation, Greek letters, and
  operators with native equation objects or properly positioned PowerPoint
  text runs and compatible math fonts. A failed
  font or PPT conversion calls for a different math representation or renderer,
  not shipping programming-style substitutes such as `C_t` or `J(pi)` when the
  paper uses mathematical notation.
- Reserve one accent for the contribution or selected path, with inherited
  machinery quiet. Use numbered phases only when they clarify reading order.
  Follow the geometry and semantic requirements below.

For a new or aesthetically unsuccessful figure, use the small candidate batch
above before detailed rendering; choose by scientific reading order,
clarity, and economy at the actual paper width. Reuse a good composition during
local repairs. Do not create a separate process report or ask the operator to
make routine layout decisions.

Open `engineer/academic-vector-figures.md` when a precise math or chart component
is needed. Locate PPT Master with `python -m argus_skill.tools.ppt_master status`; the
reported `skill_root` contains the toolkit instructions, layout references,
`scripts/svg_quality_checker.py`, `scripts/svg_to_pptx.py`, and
`scripts/pptx_to_svg.py`. Use `engineer/presentation-master.md` to install it if
needed. Use native shapes and text, then inspect the converted PPTX through its
rendered output as well as the vector figure at the manuscript's actual width.

## Craft the PPT as a scientific figure

1. Reduce the claim to one visible transformation or comparison before adding
   labels. Reuse the same visual object on both sides so a reviewer can see
   what changed. For a coding paper, draw the same event stream above aligned
   codewords; for sparse inference, show the candidate set shrinking while
   the selected token remains fixed. Derive any concrete example from the
   method and mark schematic quantities as such.
2. Give information a shape. A sequence is a row of tokens, a code is aligned
   bit fields, a set is a cluster or row of candidates, and an interval is a
   span with endpoints. Use a module box only for a real module. Do not turn
   every sentence or formula into another card.
3. Lay out a quiet backbone, then place the contribution where the eye should
   land. Use a clear overview with groups and focused detail views where the
   mechanism needs them. Show the load-bearing internal operations, interfaces,
   and feedback; a module name alone does not explain a contribution. Let the
   mechanism determine the number of panels, preserving readable type and a
   clear reading order. Move supporting derivations into the caption.
4. Use a small spacing unit at the final paper size, such as 4 pt. Align shared
   baselines and edges, give labels comfortable padding, and reserve a wider
   gap between semantic groups than between objects inside a group. Check
   optical balance after geometric alignment; a long formula needs more room
   than a short label, not a smaller font.
5. Keep white space active. Use dark ink for reading, navy for the shared
   mechanism and teal for the meaningful contrast; pale fills only delimit a
   needed group. Keep the same color meaning across panels and results plots.
   Avoid backgrounds, shadows, gradients, decorative icons, and rounded cards
   that contribute no information. Color must also work in grayscale.
6. Route connectors through reserved corridors, dock them at the actual
   object boundary, and use a consistent light stroke and arrowhead. Branches
   should originate at one explicit decision, not from nearby label text.
   Keep return paths outside the forward flow and prevent ambiguous crossings.
7. Build reusable native PPT groups for repeated tokens, fields, operators,
   and labels. Use shared sizes and styles, not hand-tuned copies. Group by
   semantic role so changes to notation or panel spacing remain easy to edit.
   A single pasted screenshot is not an editable diagram.
8. Inspect the rendered PPT at manuscript width and beside the chosen paper
   references. First check the silhouette and reading order without zooming;
   then inspect every label, connection, formula, and crop. Fix the largest
   composition problem before cosmetic details. Render the repaired PPT again
   and confirm that the formal PDF/PNG and manuscript contain this version.

Two conversion details need special care. Native theme faces such as `+mn-lt`
and `+mj-lt` can resolve to a different font from the authoring SVG; inspect
the actual PPT theme and its glyph coverage. Use ordinary letters positioned
at real subscript/superscript baselines when modifier-letter glyphs are absent.
For a short multiline label, use separate native text objects with explicit
baselines when the converter does not preserve line breaks. A merged line or
an automatic text-box wrap must not clip the figure or alter its notation.

These are drawing decisions for the Engineer, not a form the operator must
fill out or a structured output template for the Reviewer.

## Choose a composition archetype first

Strong published figures reuse a small set of compositions. Pick the one that
fits the paper's actual claim before drawing anything. No archetype is the
default for every paper. The exemplar names below illustrate structures;
they do not replace inspecting relevant figures from the current paper's area.

| Archetype | Use when | Structure | Exemplars |
|---|---|---|---|
| Pipeline strip | The contribution has a traceable forward transformation | A shared visual spine carries concrete inputs and intermediate representations through the real operations; expose the contribution inside its group and distinguish genuine training or feedback paths | RAG, InstructGPT, DreamFusion |
| Contrast diptych | The contribution is best stated as a delta against a standard approach | Two panels, old left and new right, drawn as the same diagram differing in exactly one visible attribute — a deleted box, a changed loss, one added matrix; the method panel may get more area | DPO, Chain-of-Thought, ReAct |
| Lineage progression | The contribution generalizes a known paradigm | Three lettered panels: two familiar paradigms, then the contribution in the terminal position; panel letters cited from the body text | VAR |
| Overview plus zoom | The novelty lives inside one block of an otherwise standard pipeline | Panel (a): the full pipeline at cartoon level showing where the block sits; panel (b): the single novel unit magnified with its internal wiring and dimensions | Stable Diffusion 3, NSA |
| Results-first teaser | The strongest claim is empirical | Figure 1 carries no architecture: a sample grid, a filmstrip contrast, or one headline plot with a bold takeaway sentence opening the caption; the mechanism moves to Figure 2 | VAR, Genie, Rho-1 |
| Coverage map | Benchmark, dataset, or evaluation papers | A color-coded taxonomy tree, spectrum bar, or specimen grid whose legend marks which parts are new; the caption carries most of the explanation | DecodingTrust, Aya |

## Design

1. State the figure's one-sentence scientific takeaway.
2. List the exact modules, labels, and connections, including each connection's
   source, target, direction, boundary port, and meaning.
3. Make the contribution unmistakable through subtraction or one minimal
   difference wherever possible — delete a box the baseline needs, mark the
   inherited parts frozen, change one token — so the baseline diagram is one
   visual edit away from yours. When subtraction is impossible, choose a clear
   primary emphasis: terminal panel position, one reserved accent color
   against a muted base, an ours-versus-existing legend, or extra area. Render
   standard inherited machinery quietly while preserving its meaningful
   structure. Secondary details should support the primary emphasis.
4. Keep color semantic: one color means one concept, identically in every panel
   and matched to the results charts. If a legend line cannot state what a
   color means, remove the color. Stay within about six categorical colors,
   color-blind safe, and legible in grayscale.
5. Budget on-canvas text by role and architectural depth: keep module labels
   short, but expose the important internal components, interfaces, and feedback
   in a complex system. Do not impose a fixed word cap that erases its mechanism.
   Use compact nested groups and additional horizontal bands when needed;
   preserve readable type and move explanatory prose into the caption.
6. Where it helps comprehension, run one concrete example through the diagram —
   an actual input and its intermediate representations — rather than only abstract
   labels.
7. Write the caption to stand alone: open with the takeaway (bold it when the
   venue style allows), walk the panels in reading order, decode every color,
   symbol, and badge, and name the contrast explicitly. Reuse panel letters and
   stage numbers as anchors in the body text. Never caption a figure "System
   architecture."

## Geometry and typography

- a clear reading order: use a dominant left-to-right flow for a real pipeline,
  but keep parallel analyses and alternatives parallel rather than inventing a
  serial dependency; return or training arrows must look different (dashed or
  a distinct color);
- connectors terminate at explicit node boundaries; no shaft or arrowhead
  enters an unrelated node, label, or panel; if arrows must cross, fix the
  layout rather than the arrows;
- one shape class per concept, used identically everywhere; every element in
  one step persists visibly into the next or its removal is the labeled action;
- annotate real dimensions where they matter and mark arbitrary counts with an
  ellipsis or a multiplier, so drawn counts are never accidentally readable as
  exact;
- no decorative 3D or gradients: every visual property either encodes a
  declared meaning or stays neutral;
- set the canvas to the final single- or double-column width before drawing,
  keep text at or above eight points at that size, and export vector;
- gloss any named component a general reviewer may not know — no bare acronym
  in a box;
- every visible name, direction, and value matches the paper and executed
  method verbatim; update affected notation in the source and matching export
  without redesigning the accepted composition.

## From an editable source to the finished figure

Never deliver an uncorrected one-shot raster as the final claim-bearing figure.
An optional image blueprint is a design reference, not scientific evidence.
Emit an editable structured source, render it, inspect the render, and repair
identified defects. Once it meets the figure requirements, preserve the selected
composition and return attention to the science.
Decompose complex figures — build panels and modules separately, then compose.

| Composition | Primary route |
|---|---|
| Pipeline strip or method architecture | Method D by default: image design blueprint and native editable PPT Master reconstruction; Method B fallback: direct native PPT composition |
| Contrast diptych, lineage panels | Reuse native PPT groups across aligned panels; change only the objects that express the scientific difference |
| Mathematical bounds, operators, or geometry | Native PPT shapes, equation objects, and mathematical text runs at final publication size |
| Panels of verbatim text (prompts, trajectories, rubrics) | Aligned native PPT text with measured wrapping, readable type, and restrained highlights |
| Exact load-bearing topology, taxonomy trees | Generate coordinates if useful, then draw explicit native PPT nodes and connectors |
| Data component inside a method figure | ECharts with actual data and vector output, composed and checked inside the native PPT |
| Results teaser | Matplotlib through Styling data figures for publication |

Inspect every render at actual publication size against the design rules above:
reading direction, clear primary emphasis, decodable legend, text budget,
notation match, font size, no crossings, and a caption with takeaway, panel
walk, and color decode.

Also judge the whole composition: does the mechanism read immediately, are
groups and emphasis clear, and does the figure look as carefully designed as
the accepted examples? Legible text and a clean export alone are insufficient.
Recompose a crowded collection of text boxes instead of only nudging labels.
For an explicitly needed redesign, compare the actual exported figure with the
starting version: stronger mechanism expression, spacing, hierarchy, and
mathematical notation should be visible. Preserve a suitable checked figure
when those requirements already hold.
Keep one canonical source for every formal export. If a PPTX is also delivered,
inspect it separately and ensure it depicts the same final composition; an old
deck must not be presented as the source of a new PDF. Name each final source
and export with its complete individual path in the handoff so it is openable.

Paper needs a complete, credible figure and a successful compile. Do not create
layout reports, exemplar collections, provenance records, or visual-review
files. The strict page-by-page visual judgment is made once, in Review.
That formal judgment belongs to the host's Reviewer after the main Engineer
returns; it is not a request for Engineer to dispatch another paper reviewer.
