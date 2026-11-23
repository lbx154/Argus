# Figure archetypes

Eleven shapes cover almost every non-plot figure in a CV/ML paper. Each has a
starting template in `templates/`; copy it and replace the content rather than
starting from an empty spec.

They split into two groups. **Architecture archetypes** (1–7) are for papers
whose contribution is a model. **Analysis archetypes** (8–11) are for papers
whose contribution is a measurement, an evaluation, or a distinction — these
have no network to draw, but they do have an argument, and the argument is
what the figure has to carry.

---

## 1. `teaser-comparison` — *why the existing way is wrong*

**Template:** `templates/teaser-comparison.yaml` · single column · page 1

Two or three stacked rows, one per paradigm, ending with **(Ours)**. Each row
is the *same* left-to-right sentence: input → the thing that differs → output.

Rules that make it work:

- Identical module colours across rows. The reader must see one thing change,
  not three different diagrams. The auditor fails a figure that draws the same
  label in two fills.
- Panel titles `(a) …`, `(b) …`, `(c) … (Ours)` in bold italic, left-aligned,
  above each row.
- Ours goes last and gets the warm `core`/`ours` fill; the baselines get grey
  or pale.
- If the rows share an output, draw the output column once on the right inside
  a dashed container spanning all rows.
- Line-style key top-right, in the space beside the first panel title
  (`figure.pad_top`).

**Failure mode:** rows that are structurally different, so the comparison is
between pictures rather than between methods.

---

## 2. `pipeline` — *what runs, in what order*

**Template:** `templates/pipeline-4stage.yaml` · double column · method section

Three to five stage columns, each a grey dashed container with a bold italic
title above it. Data flows left to right; the repeated block inside the
contribution stage gets a tinted wash, a dashed border in the same hue, and a
`×L` badge.

Rules:

- **Three to five stages.** Two is a block diagram; six will not fit at 7 pt.
- The contribution stage is the widest and the only one with a tinted wash.
- Arrows *within* a stage take that stage's deep tint; arrows *between* stages
  are black. This lets a reader trace a path without a legend.
- Anchor the two ends in reality: real input crops on the left, a real output
  render on the right.
- Loops are drawn once with a badge, never unrolled.

**Failure mode:** every stage the same width and the same colour, so the figure
says "there are four steps" and nothing else.

---

## 3. `module-detail` — *what the new block actually computes*

**Template:** `templates/module-detail.yaml` · single column · method section

One dashed container, the block expanded, operators explicit (`⊕`, `⊗`),
tensor shapes annotated as notes (`N × C`, `N × K × C`), the residual path
drawn as a dashed bus.

Rules:

- Every symbol in the equations should appear here, and nothing that does not.
- Shapes go on the *edges* as notes, not inside boxes.
- The residual/skip path is dashed and routed outside the main column.
- Keep it to ~8 elements; a bigger one belongs in the supplement.

**Failure mode:** a second copy of the pipeline figure at a different zoom.

---

## 4. `attention-tokens` — *what the interaction pattern is*

**Template:** `templates/attention-tokens.yaml` · double column

Token banks as grids of small squares, coloured **by frame or by modality**,
carried left to right through tall thin attention bands with rotated labels.

Rules:

- Token colour encodes provenance and never changes along the row — that is the
  whole point of the figure.
- The attention operator is a pale full-height band, not a box; it is a region
  the tokens pass through.
- Special tokens (register, camera, CLS) get one distinct colour and a note.
- If causality matters, show it: the fan of lines should be visibly triangular.

**Failure mode:** drawing attention as a box called "Self-Attention", which
tells the reader nothing they did not already know.

---

## 5. `streaming-worldmodel` — *how state persists across steps*

**Template:** `templates/streaming-worldmodel.yaml` · double column

Nested containers: the model, inside it the layer, inside that the block. Two
or three coloured input streams that the model reconciles, and a time rail
along the bottom marking history / now / future.

Rules:

- Colour the *streams*, not the boxes: the caption "Historical Gaussians" is
  set in the same dark red as the arrow that carries it.
- Nesting depth 3 maximum; use solid thin borders for nesting and reserve grey
  dashed for the outermost stage.
- The time rail is the cheapest way to say "this runs online" — one row of
  small squares, past filled, future empty, ego vehicle at `T`.

**Failure mode:** a recurrent method drawn as a feed-forward pipeline, so the
reader cannot tell what is carried over.

---

## 6. `dual-branch` — *how the two paths relate*

**Template:** `templates/dual-branch.yaml` · double column

Teacher–student, momentum encoder, contrastive pair, consistency
regularisation. The two branches run parallel and differ in exactly the ways
the paper claims; a tinted region around each carries the asymmetry
(*updated* vs *EMA, stop-grad*).

Rules:

- Draw the branches at the same y-pitch with the same module order. Any
  difference in layout will be read as a difference in the method.
- Name the two copies apart — `encoder f_θ` and `encoder f_ξ`, not `encoder`
  twice. The auditor fails a figure that draws the same label in two fills,
  and renaming is almost always the better fix than silencing it.
- The EMA / stop-gradient link is dashed and routed outside both branches.
- The loss sits where the branches meet, in the `loss` role.

**Failure mode:** two branches drawn at different sizes, so the reader cannot
tell which differences are claims and which are drafting accidents.

---

## 7. `gated-module` — *what you changed and what you froze*

**Template:** `templates/gated-module.yaml` · single column

Adapters, LoRA, FiLM, prompt tuning, calibration heads. One lane passes
through untouched (dashed, labelled *frozen*); the other lanes are modulated
by gates produced from a conditioning input.

Rules:

- **Stagger the operators horizontally** so each gate rises straight into the
  operator it drives. Two operators stacked vertically and both fed from below
  guarantees a crossing; offsetting them removes it entirely.
- The frozen lane is dashed and grey-blue; the modulated lanes carry colour.
- Put the equations directly under the figure, using the same symbols.

**Failure mode:** a three-column layout where the pass-through arrows run
straight through the middle column.

---

## 8. `blindspot-teaser` — *what the standard metric cannot see*

**Template:** `templates/blindspot-teaser.yaml` · single column · page 1

For a paper whose contribution is a distinction. Two evaluators read the same
system output, but one reads a strict subset of the components. **Draw the
subset relation as nested regions** — then each evaluator needs exactly one
arrow, and nothing crosses.

Rules:

- Nesting is the argument. If the two evaluators' inputs genuinely overlap
  rather than nest, this is the wrong archetype.
- Put the two headline numbers directly under their evaluator, in that
  evaluator's colour, with the before → after arrow inline.
- One italic line at the bottom states the thesis. It is the only sentence in
  the figure.

**Failure mode:** drawing both evaluators as peers fed by three separate
arrows, which produces a crossing and hides the containment.

---

## 9. `study-overview` — *the shape of the empirical argument*

**Template:** `templates/study-overview.yaml` · double column

The `pipeline` archetype for papers with no architecture: data and regimes →
controlled design → diagnostics → remedies and their limits. Each stage is a
step of the argument, not a layer of a network.

Rules:

- Stage titles are the four moves of the paper, in the order the Results
  section makes them.
- Use `framestrip` for observation windows and masking patterns, and `dist`
  for "a predictive distribution" — both say the thing without faking data.
- The negative result belongs in the figure. The stage that shows where the
  remedy stops working is usually the most cited panel.

**Failure mode:** a four-box flowchart that could describe any paper.

---

## 10. `factorial-2x2` — *what the controlled design isolates*

**Template:** `templates/factorial-2x2.yaml` · single column

Two binary factors crossed into four conditions. The axis headers do as much
work as the cells: a reader must be able to read one row and one column as
controlled contrasts.

Rules:

- Row and column titles name the *factor*; the header labels name the *levels*.
- Put the outcome number inside each cell. A factorial with no numbers is a
  diagram of an experiment nobody ran.
- Exactly one cell carries the claim; give it the `ours` fill and a heavier
  outline. The others stay pale.
- State the invariance in the margin — "left column: metric identical to A".
  That sentence is why the design works.

**Failure mode:** four equally coloured boxes, so the reader cannot tell which
cell the paper is about.

---

## 11. `taxonomy` — *where this work sits*

**Template:** `templates/taxonomy.yaml` · double column

A partition of the field with the paper's cell marked. Common in surveys,
position papers, and introductions that must establish an empty cell exists.

Rules:

- Two or three axes, three or four leaves each. More becomes a table.
- **Only the occupied cells carry colour.** Everything else is grey; that is
  the whole rhetorical move.
- Do not invent categories to make the gap look larger — a reviewer who works
  in one of the greyed cells will notice.

**Failure mode:** a tree that classifies the field so that exactly one cell is
empty and it happens to be yours.

---

## Choosing between them

| The reviewer's question | Archetype |
|---|---|
| "Isn't this just X?" | teaser-comparison |
| "What are the moving parts?" | pipeline |
| "Does the maths match the wiring?" | module-detail |
| "Why is this attention pattern better?" | attention-tokens |
| "What is remembered between frames?" | streaming-worldmodel |
| "How do the two branches relate?" | dual-branch |
| "What exactly did you change?" | gated-module |
| "What can the standard metric not see?" | blindspot-teaser |
| "What is the shape of the study?" | study-overview |
| "What did the controlled design isolate?" | factorial-2x2 |
| "Where does this sit in the field?" | taxonomy |

A paper usually needs one teaser-class figure (`teaser-comparison` or
`blindspot-teaser`) plus one or two others. A third framework figure is almost
always the sign of a method that has not been factored clearly.
