---
name: "Writing for the selected venue"
description: "Write the complete paper with the selected venue's author kit, in the order strong papers are written."
---

# Writing for the selected venue

Use this only in Paper after Experiment clears the paper-entry bar. Read
the research notes in `RESEARCH_NOTES.md`, direct evidence, the selected venue
in the project state, and that venue's current official author kit.
Read `references/paper-writing-craft.md`
once before drafting and keep it open while revising; it is the how, this file
is the order.

Conceptual, method, and architecture figures follow
`paper-framework-figure-studio.md`: **Method D is the default**, using an actual
image blueprint and editable PPT Master reconstruction; **Method B is the
fallback** using direct native PPT design when D is
unavailable or unsuitable. Do not introduce a separate SVG workflow.

## The standard

The standard is a strong accepted paper at the selected venue, the kind the
exemplar skill has you read. There is no house quota for sentences, words,
numbers, or caption format; the claim decides the form. The abstract is as long
and as numerical as the venue's norm and the claim require: a large speedup is
stated as a speedup, a narrow margin with its uncertainty, a mechanism finding
perhaps with no number at all. In prose, give a number the precision the
comparison needs (usually two or three significant digits) and keep full
precision in tables; a paragraph that has become a list of numbers has stopped
arguing. Say plainly what the evidence establishes, state each limit once where
it matters, and hedge a sentence only when the evidence for that sentence is
uncertain. Keep the complete method, baseline, control, adverse-result,
uncertainty, and scope coverage in the paper; selection changes where evidence
lives, never whether it is there.

## 1. Shape the argument before writing prose

Write down, before any section:

- the thesis sentence: "This paper shows that X, because Y, as evidenced by Z";
- the contribution claims, each paired with the results subsection, figure or
  table that will carry its evidence;
- the figure and table plan (Figure 1 explains the idea or mechanism; the first
  table normally carries the main result), and the page budget per section under the
  venue's limit. Apply "Plan the manuscript length" in
  `research-paper-playbook.md`: a full-length paper defaults to using nearly all
  permitted body space, with official counting rules and a section budget.

Assign the complete evidence to roles while planning: **headline** (establishes
the thesis; may recur where each location has a distinct job), **mechanism**
(why it works or fails), **disambiguating control** (rules out an alternative),
**scope-changing** (changes the claim or its boundary), **completeness** (makes
the comparison whole without changing the inference). Give each item a
canonical full location; methods and tables keep complete definitions and
matrices, prose selects what changes the inference. Moving evidence to the
appendix changes its placement; it does not remove it. Adverse or null results
that change the headline interpretation stay in the main reader path. These role words are planning
vocabulary and never appear in the manuscript.

## 2. Write in this order

1. **Draft 0 introduction**: stakes, structural gap, key idea, one-paragraph
   mental model, contribution claims. Rough, disposable, written to fix what
   the results must establish.
2. **Results**, organized by the claims, each cluster closed by a takeaway that
   states the pattern and its implication. Then **Method**: develop the
   principle-level analysis from the length plan, explaining the mechanism,
   assumptions, derivations, and design tradeoffs in depth. **Setup** provides
   reproducibility detail; it does not replace this reasoning.
3. **Final introduction**, from a blank page, now that the results stand: every
   claim maps to a results subsection and the preview carries the real
   headline numbers. Draft 0 is reference material, not the starting text.
4. **Related work**: the closest work named, the actual technical difference
   stated, critiques grounded in numbers or cases.
5. **Conclusion**, then **abstract** last: problem, insight or mechanism,
   decisive evidence, what follows; the final sentence lands on the
   contribution, not on a caveat.
6. **Compression pass** over the whole draft: remove what serves no explicit
   claim, move detail the reader does not need on first pass to the appendix,
   protect every claim, number, named baseline and limit. There is no target
   reduction fraction. Develop principle-level analysis according to the
   selected submission type's length target.
7. **Stranger's read**: the questions in the craft reference, section 11. Fix
   what fails before compiling the final PDF.

Build one confident thesis around the method's strongest supported win. Follow
the selected venue's expected reader path and required end matter. Explain the
real mechanism and the actual evaluated implementation. Compare against real,
strong, published baselines under fair information and resource conditions.
Include every intended claim-bearing experiment, figure, table, and citation.

- Do not write an experiment chronology, and do not present unfinished
  development as a finding. A clear thesis that the method helps only under
  identified conditions, or that an expected effect does not hold, is a
  legitimate paper when its evidence is as complete as a positive result would
  need.
- Keep internal paths, role names, workflow language, and development history
  out of the manuscript. Evidence-role words (headline, mechanism, control,
  scope, completeness) and terms for internal task limits, declarations of
  readiness, stage decisions, generated outputs, missions, rounds, transfers
  between roles, checking tools, and inspections never appear in the paper.

## 3. Figures and tables

Every figure and table carries a scientific claim. For a full paper, aim for
three informative figures and include at least two distinct scientific figures.
Figure 1 should explain the method or central mechanism; the others normally
show the main comparison and an ablation, diagnostic, or generalization result.
Choose distinct questions from actual evidence, without decorative filler or
renumbered pieces of one diagram. Table 1 should normally present the main
quantitative result. For a method pipeline, open `paper-framework-figure-studio.md`
and design the current code and paper as a composed, grouped figure through
PPT Master; keep the native editable PPTX and include its vector PDF export.
Use readable publication-scale
typography and conventional axes, units, captions, and uncertainty. A caption
tells the reader what to see: the question the figure answers, the comparison
conditions that matter, and the decisive number when the number is the point or
the pattern when the pattern is the point. The visual carries the complete
matrix; the caption identifies its reader-facing structure rather than reading
every cell aloud.

Keep most effort on scientific contribution, experiments, and explanation.
Substantial drawing can run in isolated parallel tasks through the figure
studio while the lead continues that work. The lead Engineer selects and
integrates the best faithful candidate, then preserves the checked composition.
New prose or a new review round does not justify another design search; update
only changed science or a concrete figure defect.

## 4. Files and finish

Maintain only `paper/main.tex`, its direct included sources, bibliography,
figures, tables, rendered paper, and the research notes at project-root
`RESEARCH_NOTES.md`. Compile under the official template, then enter Review.
Scientific judgment, strict visual inspection, and academic-language polishing
happen only there.
