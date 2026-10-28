# Writing the research paper

Use this for the paper step: writing a complete paper from the experiment's
confirmed evidence, compiling it, and preparing it for the selected venue.

## What the paper should accomplish

Produce a complete manuscript that argues the claim as stated in `METHOD.md`,
led by the strongest supported result. The paper is written only when that
claim is supported by the experiment's evidence; a narrowed claim, a
"restricted case" or a negative result is not a paper unless the operator has
changed the claim. Aim for the clarity, confidence, technical density, and
visual finish of the strongest papers at the selected venue.

The paper is an argument, not an experiment report. It is organized around a
scientific claim: why the problem matters, what insight changes the solution,
how the mechanism follows, what evidence distinguishes it from alternatives,
and what the result changes for the field. Experiments support that chain; they
do not become the narrative.

## Plan the manuscript length

For a full-length conference paper, default to a substantive main body that
uses nearly all of the selected track's permitted space. The official maximum
is a compliance ceiling, not an official minimum; the near-limit length is our
writing target. An operator-requested short paper, extended abstract,
section-only revision, or a track with different length norms overrides this
default. Never silently switch tracks to fit a short draft.

Before drafting, verify the current official venue, track, and submission type,
the applicable page or word limit, and what counts toward it (`venue.md`). Set a
section budget aimed at the final allowed body page and calibrate density
against accepted papers in that same track. Do not infer body length from total
PDF pages: references, appendices, limitations, and ethics sections count only
as the official rules specify. If the limit is unknown, resolve it rather than
assuming eight pages.

Develop the argument toward this target throughout drafting, not only when
something is missing. Expand principle-level analysis: explain why the problem
has its structure, how the mechanism follows from the assumptions, derive the
relevant relationships step by step, and explain design choices, tradeoffs,
boundary cases, and differences from alternative approaches. Use worked
conceptual examples when they clarify the reasoning.

Experiments support the argument; they are not its organizing structure.
Interpret results in terms of the mechanism and its predictions instead of
expanding run chronology, setup inventories, or metric-by-metric reporting.
Distinguish derivation, proposed explanation, and empirical observation. After
compiling, compare the actual counted body extent with the target and keep
developing the principle-level explanation where it is still terse. Record the
target and the actual counted extent in `RESEARCH_NOTES.md`.

## How to build the argument

1. Read `RESEARCH_NOTES.md`, the selected venue's requirements, and its current
   official author kit. Before prose, classify the complete evidence as
   headline, mechanism, disambiguating control, scope-changing, or completeness
   evidence; give each item a canonical full location and any repeat locations.
2. Download a small set of strong open-access accepted, Oral, Outstanding Paper,
   or Best Paper examples from the selected venue and closest area. Learn
   argument structure, pacing, Figure 1, table and caption design, typography,
   and page composition without copying prose, figures, data, or scientific
   content.
3. Write a confident thesis-driven paper led by the problem, insight, mechanism,
   and strongest result, in the order strong papers are written: a scaffold
   introduction, then Results organized by the claims with a takeaway after
   each cluster, then Method, then the final introduction from a blank page
   once the results stand, then Related Work, Conclusion, and the abstract
   last (`paper-drafting.md`). State supported contributions plainly. Do not
   narrate experiment chronology, assurances that procedures were followed,
   internal uncertainty management, or defensive caveat chains.
   `writing-craft.md` is the reference for every sentence-, paragraph- and
   section-level choice.
4. Make every section advance the central thesis. Organize Results by the
   questions needed to establish the claim, not by run order, implementation
   milestone, or "Experiment 1/2/3." A reader should recover the argument from
   the headings, the takeaways and the captions alone.
5. Include every claim-bearing experiment, fair comparison, control, ablation,
   citation, figure, table, limitation, and venue-required section the thesis
   needs. Keep complete method and result matrices in Methods, tables, or the
   Appendix while prose selects and interprets the entries that change the
   current inference. Selection changes emphasis, never scientific coverage.
6. Write to the standard of a strong accepted paper at the selected venue. There
   is no house quota for abstract length, number density, or caption format: the
   claim decides how long, how numerical, and how hedged each passage is, and the
   venue's accepted papers show the norm. Headline numbers go where they
   establish the claim; a caption tells the reader what to see. Repetition is
   allowed when it serves a different section role; repeated matrix recitation
   is not.
7. Resolve citations against primary sources and keep claims consistent with
   the executed code and raw results (`sources-and-citations.md`). The Method
   section and the claims follow `METHOD.md`; a deviation from the selected
   route is named there before it appears in the manuscript.
8. A figure earns its place when the reader must see something rather than
   parse it: a mechanism the prose cannot make visible, a comparison whose
   pattern across conditions is the point, an ablation whose shape is the
   argument. A full paper usually needs a few such figures, each answering a
   different question from real evidence; aim for three and include at least
   two distinct scientific figures, normally the mechanism, the main
   comparison, and an ablation, diagnostic, or generalization result. A finding
   that is one number, or a list the reader looks up rather than compares,
   belongs in a table or a sentence, and a duplicate panel or a decorative image
   adds nothing a reviewer will credit. Data figures follow `data-figures.md`;
   the method or Figure 1 composition follows `method-figures.md`. Every figure
   has an editable source, a publication-size export, and its
   `paper/figures/<stem>.source.json` record. Reuse a suitable existing figure
   across writing rounds and prose-only edits; once a figure is checked, keep
   its composition and repair only changed science or a concrete defect. Most
   effort stays on scientific substance; cosmetic variations must not dominate
   the schedule.
9. Compress after expanding to the length target: the final pass removes what
   serves no explicit claim and moves first-pass-unnecessary detail to the
   appendix while protecting every claim, number, named baseline and limit.
   Then read the paper once as a stranger and fix what fails.
10. Compile with the selected venue's current rules (`venue.md`).

The paper step writes; it does not develop the method. A new run belongs here
only when the manuscript exposes a specific evidence gap, such as a missing
baseline or an unrepresented model family or scale, and it is run at the scale
the claim needs; a new mechanism variant never does. The manuscript reports
only what was run: no simulated, placeholder, projected, or illustrative
results appear anywhere in it, and in particular no human-study numbers that
were not collected from participants. A study that did not run is outside the
claims and may be one sentence of future work.

A request for part of a paper (figures, a section, a revision) produces exactly
the requested work with this guide's figure and writing skills and stops after
its review. It does not need a full manuscript or `RESEARCH_NOTES.md`.

Writing includes the normal checks needed while writing. The final scientific,
visual, language, and whole-paper judgments are made together in the review
step, not here.

No defensive writing: lead with the strongest supported result, state the
contribution plainly, and let the evidence carry it. Limitations are one honest
paragraph of accurate, specific statements, not the framing of the paper; they
do not enter the title, abstract, introduction, or conclusion as hedges, and a
caveat chain never stands in for a clear statement.
