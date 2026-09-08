---
name: "Writing the research paper"
description: "The guide that defines Paper: write a complete paper from confirmed positive evidence, compile it, and prepare it for the selected venue."
---

# Writing the research paper

## What the paper should accomplish

Produce a complete manuscript that argues the strongest contribution the
confirmed Experiment evidence supports. Aim for the clarity, confidence,
technical density, and visual finish of the strongest papers in the selected
venue.

The paper is an argument, not an experiment report. It is organized around a
scientific claim: why the problem matters, what insight changes the solution,
how the mechanism follows, what evidence distinguishes it from alternatives,
and what the result changes for the field. Experiments support that chain; they
do not become the narrative.

## How to build the argument

### Plan the manuscript length

For a full-length conference paper, default to a substantive main body that
uses nearly all of the selected track's permitted space. The official maximum
is a compliance ceiling, not an official minimum; the near-limit length is our
writing target. An operator-requested short paper,
extended abstract, section-only revision, or a track with different length
norms overrides this default. Never silently switch tracks to fit a short draft.

Before drafting, verify the current official venue, track, and submission type,
the applicable page or word limit, and what counts toward it. Set a section
budget aimed at the final allowed body page and calibrate density against
accepted papers in that same track. Do not infer body length from total PDF
pages: references, appendices, limitations, and ethics sections count only as
the official rules specify. If the limit is unknown, resolve it rather than
assuming eight pages.

Develop the argument toward this target throughout drafting, not only when
something is missing. Actively expand principle-level analysis: explain why the
problem has its structure, how the mechanism follows from the assumptions,
derive the relevant relationships step by step, and explain design choices,
tradeoffs, boundary cases, and differences from alternative approaches.
Use worked conceptual examples when they clarify the reasoning.

Experiments support the argument; they are not its organizing structure.
Interpret results in terms of the mechanism and its predictions instead of
expanding run chronology, setup inventories, or metric-by-metric reporting.
Distinguish derivation, proposed explanation, and empirical observation.
After compilation, compare actual counted body extent with the target and
continue developing the principle-level explanation where it is still terse.
Keep the target and actual counted body extent in the existing research notes;
do not create a new report or validation-only task.

1. Read the research notes in `RESEARCH_NOTES.md`, the selected venue profile, and the current official
   author kit. Before prose, classify the complete evidence as headline,
   mechanism, disambiguating control, scope-changing, or completeness evidence;
   assign each item a canonical full location and any repeat locations.
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
   last. State supported contributions plainly. Do not narrate experiment
   chronology, assurances that procedures were followed, internal uncertainty management, or
   defensive caveat chains. `engineer/references/paper-writing-craft.md` is
   the craft reference for every sentence-, paragraph- and section-level
   choice.
4. Make every section advance the central thesis. Organize Results by the
   questions needed to establish the claim, not by run order, implementation
   milestone, or "Experiment 1/2/3." A reader should recover the argument from
   the headings, the takeaways and the captions alone.
5. Include every claim-bearing experiment, fair comparison, control, ablation,
   citation, figure, table, limitation, and venue-required section needed by the
   thesis. Keep complete method and result matrices in Methods, tables, or the
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
   the executed code and raw results.
8. Produce editable figure sources, publication-size exports, and a readable
   rendered paper. For the method pipeline, use `engineer/research-svg-pipeline.md`:
   synthesize the drawing from the current manuscript and executed code, with
   compact horizontal, staggered geometry and Times New Roman. Include its
   vector PDF after Introduction, targeting page 2 or 3, and keep the editable
   SVG source. Invoke the drawing component only when a figure is needed;
   reuse an existing suitable figure across writing rounds and prose-only edits.
9. Compress after expanding to the manuscript-length target: the final pass removes what serves no explicit
   claim and moves first-pass-unnecessary detail to the appendix while
   protecting every claim, number, named baseline and limit. Then read the
   paper once as a stranger and fix what fails.
10. Compile successfully with the selected venue's current rules.

Paper writes; it does not develop the method. A new run belongs in this stage
only when the manuscript exposes a specific evidence gap, such as a missing
baseline or an unrepresented model family or scale, and it is run at the scale
the claim needs; a new mechanism variant never does. The manuscript reports
only what was run: no simulated, placeholder, projected, or illustrative
results appear anywhere in it, and in particular no human-study numbers that
were not collected from participants. A study that did not run is outside the
claims and may be one sentence of future work.

A direct request for part of a paper (figures, a section, a revision) produces
exactly the requested work with this guide's figure and writing skills and
stops after independent review. It does not require a full manuscript, a venue
profile, or research notes.

Paper includes the normal checks needed while writing. The scientific, visual,
language, and whole-paper judgments are made together in Review; do not make
them separately in Paper.
Limitations remain accurate and specific, but they do not dominate the title,
abstract, introduction, or conclusion when the evidence supports a strong claim.

## When the draft is ready

The full paper, bibliography, figures, tables, includes, and rendered output are
present and mutually consistent. The counted body extent meets the planned
length target and the main body develops the principles, mechanism, and design
reasoning in depth. Being below the legal maximum alone does not establish readiness.
Manager alone advances the stage.

## Research notes

Replace the research notes at project-root `RESEARCH_NOTES.md`, beginning with
`# Research notes — Paper stage`. Include only the current manuscript location,
central thesis, evidence roles and placements,
venue, manuscript-length target and actual counted body extent, and any known
issue Review must inspect. Do not create another drafting
or format report.

## When another skill would help

Start with this guide. Open one specialist skill only for the current paper
task, then return here. Do not read all the sources in advance.

| When needed | Open | Use it for |
|---|---|---|
| The venue is not selected | `engineer/venue-format-research.md` | Choose a fitting venue from current official sources |
| The argument or full draft must be written | `engineer/venue-paper-drafting.md` | Draft under the selected author kit, in the order strong papers are written |
| A passage, section or the abstract needs to read like a strong paper | `engineer/references/paper-writing-craft.md` | Introduction moves, results by claims with takeaways, numbers and precision, confidence without defensive patterns, compression |
| Strong paper structure or visual calibration is needed | `engineer/paper-exemplar-pdf-learning.md` | Study open-access Oral, Outstanding, or Best Papers |
| A material citation is uncertain | `engineer/citation-check.md` | Resolve and repair it from primary sources |
| Data results need paper figures | `engineer/paper-chart-styling.md` | Produce consistent publication-size data charts |
| A method pipeline or architecture overview is needed | `engineer/research-svg-pipeline.md` | Draw a compact horizontal SVG from code and paper, with Times New Roman |
| A conceptual or method figure is needed | `engineer/research-visualization-router.md` | Select the faithful rendering route |
| Figure 1 needs an editable composition | `engineer/paper-framework-figure-studio.md` | Build the conceptual figure and final export |
| Compilation or venue structure is uncertain | `engineer/venue-format-preflight.md` | Compile against the official author kit |

Specialist Skills produce parts of the manuscript. They do not define stage
completion or run scientific, visual, language, or whole-paper review passes.
