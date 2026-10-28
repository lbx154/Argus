# Reaching a judgment on the paper

Use this in the Review step: the Reviewer considers the science, figures, language and the
paper as a whole and reaches an independent judgment about whether the work holds; the
Engineer makes every repair that judgment asks for.

## The judgment to reach

Independently decide whether the current complete paper is scientifically sound,
publication-ready, and worth accepting at the selected venue. Judge it against strong accepted
and best-paper-level work there. Meeting the letter of the requirements alone is not enough.

Apply "Plan the manuscript length" in `paper.md` to a full paper. Compare its rendered body
extent, under the exact track's counting rules, with the writing target. Judge the depth of
principle-level analysis: does the reader understand why the mechanism follows, what the
assumptions imply, and why the design choices differ from alternatives? Ask for expansion of
terse reasoning, derivations, or tradeoffs. Experiments should support this argument, not
turn the paper into an experiment report. Respect an explicit short-paper or partial-edit
request.

## What to read

Start with `paper/main.tex`, the rendered `paper/main.pdf`, and the Engineer's latest letter,
then follow only direct claim-critical references to code, configuration, raw rows,
evaluators, or primary sources. Do not crawl historical research files. Make one pass over the
PDF alone, as a stranger would read it (`language-review.md`), and inspect every page, figure
and table at the size it will be printed. The specialist passes are in `peer-review.md` (science),
`strongest-objection.md` (the objection that would sink it) and `language-review.md`
(argument and prose).

## Repairs happen inside Review

The Engineer owns the substantive repairs and high-impact improvements the Reviewer asks for,
including a decisive baseline, control, method test, or explanatory analysis. This is full
conference peer review of the contribution, novelty, methods, experimental design, results,
and conclusions, alongside presentation. The Engineer runs the needed scientific work during
Review, revises the paper, and returns it for independent re-review. Nothing goes back to an
earlier step, and a normal scientific repair is never a request to restart the earlier work.

Keep most effort on scientific substance. Identify the next decisive method, baseline,
experiment, or analysis while substantial figure work goes on through `method-figures.md`.
Review the current science while a drawing is in progress; defer only the affected figure's
final inspection. A cosmetic search must not keep an otherwise acceptable paper in an endless
loop.

For a scientific revision, finish a coherent piece of evidence before returning to the
Reviewer. Bundle the related implementation, public-entry-point, analysis and manuscript
repairs from the current findings; independent work can run in parallel. A preliminary check
guides the next experiment, not another whole-paper review after every small edit.
Conversely, the Engineer need not solve the entire paper or reach the final rating before
returning useful new evidence.

If a method, oracle, evaluator or cost definition changes, trace which claims, panels and
controls depend on it. Re-establish those comparisons with the current execution contract; do
not combine a corrected focal experiment with older broad panels and call the whole result
validated. Keep unaffected evidence and clearly identified earlier results. A feasible method
or experiment improvement receives a real attempt; a claim that fails its test is handled as
the README says for the claim's owner, never narrowed to fit the code.

For a new mathematical guarantee that carries the contribution, complete the claim and its
justification alongside the implementation: state the domain and assumptions, establish the
result, handle relevant boundary cases, and explain how the executed algorithm satisfies
them. Numerical tests check implementation fidelity; they do not prove a claim over all
admissible inputs. This does not require adding a theorem to an empirical paper that makes no
such guarantee.

Claims about component effects or interactions require the relevant factors to be compared on
identical experimental units and conditions. A targeted shared-state result and a separate
independent-state panel do not establish an interaction when their inputs or selections
differ. Run the discriminating paired comparison and preserve all its outcomes. Keep retained
method variants explicitly selectable or preserve their actual executable source, so an
updated default cannot silently change what a published reproduction command computes.

For a mechanism discovered in selected examples, distinguish exploration, replication and
independent confirmation. Check what data were inspected when choosing the regime, including
outcomes used indirectly through a proxy. Unselected cases from that search are still
development evidence. Freeze the scientific comparison and test new units in the proposed
regime when the claim needs generalization; a null panel in a different layer or population
does not settle that question. Preserve useful running attempts and their exploratory
results, then add the decisive confirmation. Do not demand that it be positive.

## A repair to the prose alone

When the findings are about reading, not science:

1. Start from the current manuscript and the Reviewer's located findings. Repair only a
   located obstacle to understanding or to the argument, preserving clear content,
   structure, wording, exact facts, complete coverage, and the abstract's claims and
   evidence.
2. Prefer adding a missing explanation or adjusting local sentence order; explain why a local
   repair is insufficient before reorganizing a section or the paper. Use `writing-craft.md`
   for how the repair should read, and do not copy review wording into the manuscript.
3. Preserve every number, comparison direction, claim scope, adverse result, material
   uncertainty, decisive control, and the complete method and result coverage. You may
   propose moving unique content in your letter, but do not remove it or change its
   scientific meaning.
4. If no concrete problem needs repair, say so and leave the manuscript unchanged. Recompile
   when manuscript inputs changed; reuse a current PDF when nothing changed.

The Reviewer then checks that the original problem is resolved and the scientific content is
preserved, reading the change itself (for example `git diff -- paper/`): a move from prose to
a table, Methods, a caption, the Appendix or a cross-reference is not a loss, and only a named
lost fact, reasoning step, scope boundary or coverage carrier is. Resolved findings close;
further revision needs a remaining or newly introduced defect, not a preference for different
wording.

## Figures and pages

Inspect every rendered page and every figure and table at publication scale. Any visible
overlap, clipping, overflow, connector penetration, wrong arrow, unreadable label, malformed
table, misleading plot, abnormal whitespace, broken float placement, or inconsistent
typography means the paper does not yet hold visually. That the paper compiled says nothing
about how it looks.

For the method pipeline, compare the actual drawing against the executed code and
manuscript: labels, branches, training/inference arrows and the highlighted mechanism must
agree. Inspect the whole composition at the included size: meaningful groups, visual
hierarchy, balanced spacing, and typography should make the mechanism immediately
understandable. A crowded collection of text boxes is visually unfinished even when its labels
are legible, and bullet lists in three boxes are not a mechanism. Inspect the existing PDF; do
not ask for a redraw merely because Review started. Default placement is after the
Introduction, preferably on page 2 or 3; fix float placement in LaTeX without redrawing.
Require repairs to surplus whitespace, unreadable labels, clipping and connector collisions,
and recompose an unfinished figure with `method-figures.md`, keeping its editable source and
re-exporting the included PDF from it. A successful render or font check alone does not
establish that a figure is visually sound.

A full paper should aim for three informative figures and include at least two distinct
scientific figures, usually the mechanism, main comparison, and an ablation, diagnostic, or
generalization result. Judge their contribution to understanding; ask for a missing scientific
view rather than decorative filler or a duplicated plot. Once a figure has passed fidelity and
publication-size inspection, close the finding and preserve its composition. Reopen it only
for a changed scientific quantity or mechanism, a specific remaining or introduced defect, or
explicit operator feedback. If a color or spacing preference does not affect clarity, leave
it alone. Update a changed data panel without redrawing the unaffected method figure.

Apply the author kit's ordinary float conventions. A page-top or page-bottom figure or table
may appear between the two pages of a continuing sentence; that alone is not broken reading
order. Natural trailing whitespace on the final references or appendix page is optional
polish. Do not demand forced placement, manual page breaks or sentence rewrites for these
alone. A required repair names a specific comprehension problem, misleading placement,
detached caption, overlap, clipping or actual illegibility.

## When the paper does not hold yet

A paper does not hold if it meets the technical requirements but is unimportant, timid,
visually unfinished, or organized around caveats instead of contribution. Do not demand
defensive qualifications that the evidence does not require.

A manuscript does not hold if it reads as an experiment report: run chronology,
implementation diary, tables without argumentative purpose, or a Results section that never
establishes the central claim. Require one explicit thesis and a section-by-section argument
in which each experiment answers a necessary scientific question.

For each required narrative repair, identify the passage or PDF location, the concrete
obstacle to understanding or inference, and the smallest repair goal. Calling a paper
report-like, unacademic, or insufficiently fluent is not enough by itself. An explanation
already clear in the surrounding context need not be repeated after every number. Keep
unaffected passages intact.

## The judgment, in the letter

The Reviewer edits nothing; its letter is the judgment. Each review states the current
assessment of the science, figures, and language; the strongest case for acceptance; the
remaining rejection-level issues; an explicit recommendation for the selected venue in plain
words, with the evidence for it and the remaining weaknesses; and the next repairs. Resolved
findings stay closed rather than being copied forward. No fixed fields or template are
needed.

Only a clearly supported recommendation at or above the operator's requested standard, with
no reject-level issue, accepts the paper; a weak accept does not finish a requested
strong-accept goal. Borderline, rejection, uncertainty, missing evidence, or a mismatched venue
returns the work to the Engineer with the repairs. A previous approval or a successful compile
is not a judgment of the current paper. Before accepting, run `checks/paper.mjs` on the
current build (see the README).

There is no count of revision rounds after which the paper passes. Do not raise the
recommendation to end a long run, lower the venue, or hide adverse evidence. Where the
contribution or evidence is too weak, give the Engineer substantive scientific repairs and
decisive experiments to run within Review.

## Constructive feedback and improvement

Help the Engineer build the strongest paper the evidence can support. Start each review with
specific verified strengths and progress, explain why those results matter, and close
resolved findings. Be encouraging and respectful without generic praise, personal criticism,
invented novelty, or inflated ratings.

Prioritize feasible improvements by scientific value. For each, identify the opportunity, a
concrete change, and a decisive validation with appropriate controls and a success criterion.
Offer creative hypotheses, method alternatives, or analyses when they could strengthen the
contribution; mark them as untested and start with the cheapest informative test. Preserve
adverse results. Distinguish acceptance blockers, actionable high-impact improvements, and
speculative future-work ideas.

Review must lead to actual changes when feasible, high-impact improvements are left, even if
the current paper already merits weak accept: explain them and return the work for revision
before accepting. The Engineer implements the feedback, says in its letter what changed and
how it was validated, and returns for independent re-review. A failed hypothesis is resolved
by repairing the implementation or the experiment until the claim in `METHOD.md` is tested as
stated, or by its owner changing that claim; agreement with the Reviewer is not a scientific
result, and a claim narrowed to fit the code is not a repair.

Aim toward strong acceptance and best-paper quality while honoring the operator's requested
minimum. Speculative future-work ideas do not hold acceptance. Do not invent endless
experiments, repeat resolved objections, or demand arbitrary rewrites merely to sustain the
loop. Do not create separate scientific, visual, language, or revision-history files, or files
declaring the paper ready.
