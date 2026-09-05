---
name: "Research Paper Playbook"
description: "The single authoritative playbook for writing a complete, compilable, venue-ready paper from confirmed positive evidence."
---

# Research Paper Playbook

## Outcome

Produce a complete manuscript that argues one important positive contribution
from the confirmed Experiment evidence. Aim for the clarity, confidence,
technical density, and visual finish of the strongest papers in the selected
venue.

The paper is an argument, not an experiment report. Its organizing unit is a
scientific claim: why the problem matters, what insight changes the solution,
how the mechanism follows, what evidence distinguishes it from alternatives,
and what the result changes for the field. Experiments support that chain; they
do not become the narrative.

## Work

1. Read `HANDOFF.md`, the selected venue profile, and the current official
   author kit. Before prose, classify the complete evidence as headline,
   mechanism, disambiguating control, scope-changing, or completeness evidence;
   assign each item a canonical full location and any repeat locations.
2. Download a small set of strong open-access accepted, Oral, Outstanding Paper,
   or Best Paper examples from the selected venue and closest area. Learn
   argument structure, pacing, Figure 1, table and caption design, typography,
   and page composition without copying prose, figures, data, or scientific
   content.
3. Write a confident thesis-driven paper led by the problem, insight, mechanism,
   and strongest result. State supported contributions plainly. Do not narrate
   experiment chronology, process compliance, internal uncertainty management,
   or defensive caveat chains.
4. Make every section advance the central thesis. Organize Results by the
   questions needed to establish the claim, not by run order, implementation
   milestone, or "Experiment 1/2/3."
5. Include every claim-bearing experiment, fair comparison, control, ablation,
   citation, figure, table, limitation, and venue-required section needed by the
   thesis. Keep complete method and result matrices in Methods, tables, or the
   Appendix while prose selects and interprets the entries that change the
   current inference. Selection changes emphasis, never scientific coverage.
6. Preserve the drafting contract: a five-sentence abstract with at least 170
   words, exact headline numbers where they establish the claim, and a numerical
   takeaway in every figure and table caption. Repetition is allowed when it
   serves a different section role; repeated matrix recitation is not.
7. Resolve citations against primary sources and keep claims consistent with
   the executed code and raw results.
8. Produce editable figure sources, publication-size exports, and a readable
   rendered paper. For the method pipeline, use `engineer/research-svg-pipeline.md`:
   synthesize the drawing from the current manuscript and executed code, with
   compact horizontal, staggered geometry and Times New Roman. Include its
   vector PDF after Introduction, targeting page 2 or 3, and keep the editable
   SVG source. Invoke the drawing component only when a figure is needed;
   reuse an existing suitable figure across writing rounds and prose-only edits.
9. Compile successfully with the selected venue's current rules.

Paper performs normal authoring checks, not a separate scientific, visual,
language, or whole-paper acceptance. Those happen together in Review.
Limitations remain accurate and specific, but they do not dominate the title,
abstract, introduction, or conclusion when the evidence supports a strong claim.

## Completion

The full paper, bibliography, figures, tables, includes, and rendered output are
present and mutually consistent. Manager alone advances the stage.

## Handoff

Replace project-root `HANDOFF.md` with `# HANDOFF — PAPER`, containing only the
current manuscript location, central thesis, evidence roles and placements,
venue, and any known issue Review must inspect. Do not create another drafting
or format report.

## Progressive disclosure

Start with this Playbook. Open one specialist Skill only for the current paper
task, then return here. Do not preload the table.

| When needed | Open | Use it for |
|---|---|---|
| The venue is not selected | `engineer/venue-format-research.md` | Choose a fitting venue from current official sources |
| The argument or full draft must be written | `engineer/venue-paper-drafting.md` | Draft under the selected author kit |
| Strong paper structure or visual calibration is needed | `engineer/paper-exemplar-pdf-learning.md` | Study open-access Oral, Outstanding, or Best Papers |
| A material citation is uncertain | `engineer/citation-audit.md` | Resolve and repair it from primary sources |
| Data results need paper figures | `engineer/paper-chart-styling.md` | Produce consistent publication-size data charts |
| A method pipeline or architecture overview is needed | `engineer/research-svg-pipeline.md` | Draw a compact horizontal SVG from code and paper, with Times New Roman |
| A conceptual or method figure is needed | `engineer/research-visualization-router.md` | Select the faithful rendering route |
| Figure 1 needs an editable composition | `engineer/paper-framework-figure-studio.md` | Build the conceptual figure and final export |
| Compilation or venue structure is uncertain | `engineer/venue-format-preflight.md` | Compile against the official author kit |

Specialist Skills produce parts of the manuscript. They do not define stage
completion or run scientific, visual, language, or whole-paper review gates.
