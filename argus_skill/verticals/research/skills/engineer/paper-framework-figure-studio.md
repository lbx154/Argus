---
name: "Composing a conceptual paper figure"
description: "Default to Method D: image-first design and editable PPT Master reconstruction; use Method B local drawing as the fallback, with refined academic colors and typography."
---

# Composing a conceptual paper figure

Use this in Paper for Figure 1 or another conceptual, method, architecture, or
taxonomy figure. Read the research notes in `RESEARCH_NOTES.md`, the current manuscript, the executed method,
and direct result sources. Create the canonical editable figure source and the
matching vector PDF and PNG. Method D is the default and Method B is the
fallback, as defined below. Use precise mathematical/vector components inside
the selected reconstruction when useful. Tool choice does not establish visual
quality; inspect the actual figure and its publication-size inclusion.

## Default Method D; fallback Method B

This is the routing contract for conceptual, method, architecture, taxonomy,
teaser, and graphical-abstract figures. Quantitative charts, including the data
panel of a mixed figure, stay on the SciencePlots/Matplotlib route.
An explicit operator choice overrides the default.

**Method D is the default: reference figures -> image-API design blueprint ->
editable reconstruction -> native PPTX through PPT Master -> paper export.**

1. Reuse an existing suitable figure or blueprint before creating another.
   A prose-only edit, compile, or new Review round does not justify regeneration.
   Ground labels and connections in the current manuscript and executed method.
   Inspect suitable published reference figures before generating a new
   blueprint; learn their composition without copying artwork. Keep source
   pointers in the existing research notes, not an exemplar collection.
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
   raw experiment data, credentials, or code. Start with one candidate; reuse it
   and repair locally rather than repeatedly calling the API for text or
   geometry fixes. Preserve the actual returned image and prompt alongside
   the drawing source, without credentials. A failed request is not a blueprint.
4. The active Engineer model reconstructs the design as editable objects, restoring
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
   If PDF/PNG previews come from SVG rather than a PowerPoint render, say so;
   do not claim an Office rendering was inspected when it was not.

**Method B is the fallback: direct local vector drawing without an image API.**
The active model designs the figure with native objects or local drawing code,
including Matplotlib, TikZ/LaTeX, or other appropriate vector tools. For precise
mathematics and physical-unit layout, use `academic-vector-figures.md`. Keep the
canonical editable source and the included vector export. SVG may be an internal
source/export format, but there is no separate SVG workflow or routing entry.
Method B does not require PPT Master or image-generation credentials. It must
preserve the same scientific fidelity and publication-size readability as D;
never relabel a Method B drawing as an API-assisted reconstruction.

## Publication style

Apply these defaults to a new figure unless the paper already has an established
style. Aim for a carefully composed academic illustration, with quiet inherited
machinery and unmistakable scientific structure.

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
  operators with Matplotlib mathtext or LaTeX/TikZ when appropriate. A failed
  font or PPT conversion calls for a different math representation or renderer,
  not shipping programming-style substitutes such as `C_t` or `J(pi)` when the
  paper uses mathematical notation.
- Reserve one accent for the contribution or selected path, with inherited
  machinery quiet. Use numbered phases only when they clarify reading order.
  Follow the geometry and semantic requirements below.

For a new or aesthetically unsuccessful figure, sketch two genuinely different
compositions before detailed rendering; choose by scientific reading order,
clarity, and economy at the actual paper width. Reuse a good composition during
local repairs. Do not create a separate process report or ask the operator to
make routine layout decisions.

Open `engineer/academic-vector-figures.md` for physical-unit Matplotlib,
mathematical typography, or TikZ/SVG composition. If PPT Master is selected,
locate it with `python -m argus_skill.tools.ppt_master status`; the
reported `skill_root` contains the toolkit instructions, layout references,
`scripts/svg_quality_checker.py`, `scripts/svg_to_pptx.py`, and
`scripts/pptx_to_svg.py`. Use `engineer/presentation-master.md` to install it if
needed. Use native shapes and text, then inspect the converted PPTX through its
rendered output as well as the vector figure at the manuscript's actual width.

## Choose a composition archetype first

Strong published figures reuse a small set of compositions. Pick the one that
fits the paper's actual claim before drawing anything:

| Archetype | Use when | Structure | Exemplars |
|---|---|---|---|
| Pipeline strip | The contribution is a method with a traceable forward pass (the default) | One horizontal band: input at far left, two to four enclosed modules, output at far right; training or feedback signals drawn as visually distinct arrows over the flow; stages may be numbered and walked in order by the caption | RAG, InstructGPT, DreamFusion |
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
   visual edit away from yours. When subtraction is impossible, use exactly one
   highlighting device: terminal panel position, one reserved accent color
   against a muted base, an ours-versus-existing legend, or extra area. Render
   standard inherited machinery in quiet gray; a figure where everything is
   equally loud says nothing.
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
  method verbatim; regenerate the figure when notation changes.

## From an editable source to the finished figure

Never deliver an uncorrected one-shot raster as the final claim-bearing figure.
An optional image blueprint is a design reference, not scientific evidence.
Emit an editable structured source, render it, inspect the render, and revise
until it meets the figure requirements.
Decompose complex figures — build panels and modules separately, then compose.

| Composition | Primary route |
|---|---|
| Pipeline strip or method architecture | Method D by default: image design blueprint and native editable PPT Master reconstruction; Method B fallback: native objects or local Matplotlib/TikZ drawing |
| Contrast diptych, lineage panels | Use one programmatic source for aligned panels; apply the delta to a shared diagram so the panels differ only where the science differs |
| Mathematical bounds, operators, or geometry | TikZ/LaTeX or Matplotlib mathtext with vector output; combine with SVG/PPT when needed for the surrounding architecture |
| Panels of verbatim text (prompts, trajectories, rubrics) | HTML/CSS with inline SVG rendered headlessly to vector PDF — the only route with a real text-layout engine; verify the render visually since headless failures are silent |
| Exact load-bearing topology, taxonomy trees | Graphviz for layout coordinates, restyled through SVG; or FigureSpec, Draw.io, browser SVG |
| Results teaser | Matplotlib through Styling data figures for publication |

Inspect every render at actual publication size against the design rules above:
reading direction, one highlighting device, decodable legend, text budget,
notation match, font size, no crossings, and a caption with takeaway, panel
walk, and color decode.

Also judge the whole composition: does the mechanism read immediately, are
groups and emphasis clear, and does the figure look as carefully designed as
the accepted examples? Legible text and a clean export alone are insufficient.
Recompose a crowded collection of text boxes instead of only nudging labels.
Compare the actual exported figure with the starting version: better spacing,
hierarchy, mathematical notation, and deliberate emphasis must be visible.
Keep one canonical source for every formal export. If a PPTX is also delivered,
inspect it separately and ensure it depicts the same final composition; an old
deck must not be presented as the source of a new PDF. Name each final source
and export with its complete individual path in the handoff so it is openable.

Paper needs a complete, credible figure and a successful compile. Do not create
layout reports, exemplar collections, provenance records, or visual-review
files. The strict page-by-page visual judgment is made once, in Review.
