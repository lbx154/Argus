---
name: "Reaching a judgment on the paper"
description: "The guide that defines Review: consider the science, figures, language, and paper as a whole, and reach an independent judgment about whether the work holds."
---

# Reaching a judgment on the paper

## The judgment to reach

Independently decide whether the current complete paper is scientifically sound,
publication-ready, and worth accepting at the selected venue. Judge it against
strong accepted and best-paper-level work there. Meeting the letter of the
requirements alone is not enough.

## What each reader can consult

What a reader may consult depends on the operation. The fresh-context Narrative
Editor reads the current manuscript, the evidence roles in the research notes
(`RESEARCH_NOTES.md`), the drafting requirements, and the latest actionable
Reviewer findings supplied for this round. Use that feedback
to repair the identified problem; do not search review history or internal
diagnostic reports, or copy reviewer-response wording into the manuscript.
Scientific loss review reads immutable
before/after source and PDF snapshots plus a direct claim-critical source only
for a concrete dispute. The cold reader receives an isolated workspace
containing only `paper/main.pdf`. The integrated Reviewer may start with
`paper/main.tex`, its rendered output, and `paper/REVIEW.md`, then follow direct
claim-critical references without recursively inspecting history.

## How to read and revise the paper

1. Preserve the pre-edit manuscript source closure and rendered PDF in internal
   mission state; do not use Git as the scientific baseline.
2. Run `narrative_edit` as a fresh-context Engineer operation. Start from the
   existing manuscript and current findings. Repair only a located obstacle to
   reader understanding or the argument, preserving clear content, structure,
   wording, exact facts, complete coverage, and the abstract's claims and
   evidence. Prefer a missing explanation or local sentence
   adjustment; explain why local repair is insufficient before widening scope.
   If no concrete problem needs repair, return without changing the manuscript.
   Compile if manuscript inputs changed or the PDF is missing or stale; reuse
   a current PDF when no input changed.
3. After the Engineer turn, the host runs the independent read-only passes concurrently.
   Engineer and integrated Reviewer must not spawn a duplicate review team:
   - `science_loss_check` compares before/after scientific completeness,
     meaning, and carriers;
   - strict page-by-page visual quality inspects the isolated current PDF;
   - `cold_read` judges argument hierarchy and academic language from the
     isolated rendered PDF only.
   An identical verified before/after snapshot needs no semantic-loss model call.
   The host may reuse PDF-only visual/cold-read assessments only for identical
   rendered bytes and review policy. Always evaluate the scientific evidence
   for the current round and reach a fresh integrated judgment.
4. Give these internal findings to the normal integrated Reviewer. Weigh the
   current supplied assessments; repeat an inspection only for a concrete
   contradiction or changed input, using the smallest decisive check. When the
   host supplied no passes, the Reviewer inspects the paper itself and that
   inspection is the assessment; missing host passes are never by themselves a
   reason to find the paper unfinished or to wait. Only that
   Reviewer controls the round and overwrites `paper/REVIEW.md`; preliminary
   passes create no project-visible report or history. Semantic-loss and cold-
   read findings begin in shadow mode: until calibration explicitly enables
   enforcement, they may guide or corroborate an existing review criterion but
   cannot be the sole reason to find that the paper does not hold yet.
5. Have the Engineer repair the identified problems and resolve any
   scientific/readability conflict. Recompile when the paper changes. Reviewer
   checks that the original problem is resolved and scientific content is
   preserved, then closes that finding. Further revision requires a remaining
   or newly introduced defect, not a preference for different wording. The host
   refreshes affected post-edit assessments before the integrated Reviewer
   reaches a judgment.

All scientific, experiment, visual, and language defects are repaired inside
Review. The stage never rolls back.

For the method pipeline, compare the actual drawing against the executed code
and manuscript: labels, branches, training/inference arrows and the highlighted
mechanism must agree. Inspect the whole composition at the included publication
size: meaningful groups, visual hierarchy, balanced spacing, and typography
should make the mechanism immediately understandable. A crowded collection of
text boxes is visually unfinished even when its labels are legible.
Inspect the existing PDF; do not
invoke the drawing component merely because Review started. Default placement
is after Introduction, preferably on page 2 or 3; fix float placement in LaTeX
without redrawing. Require repairs to surplus whitespace, unreadable
labels, clipping and connector collisions. Recompose an unfinished figure with
`engineer/paper-framework-figure-studio.md` and PPT Master; use
`engineer/research-svg-pipeline.md` for a concrete repair to an existing suitable
SVG. Regenerate the included PDF. A successful
render or font check alone does not establish that the figure is visually sound.

Find that a paper does not hold yet if it meets the technical requirements but
is unimportant, timid, visually unfinished, or organized around caveats instead
of contribution. Do not demand defensive qualifications that the evidence
does not require.

Find that a manuscript does not hold yet if it reads as an experiment report:
run chronology, implementation diary, tables without argumentative purpose, or a Results
section that never establishes the central claim. Require one explicit thesis
and a section-by-section argument in which each experiment answers a necessary
scientific question.

For each required narrative repair, identify the passage or PDF location, the
concrete obstacle to understanding or inference, and the smallest repair goal.
Calling a paper report-like, unacademic, or insufficiently fluent is not enough
by itself. An explanation already clear in the surrounding context need not
be repeated after every number. Keep unaffected passages intact.

## Recording the judgment

`paper/REVIEW.md` contains `Scientific:`, `Visual:`, and `Language:` assessments,
the strongest case for a venue reviewer to accept the paper, issues that would
justify rejection at the venue, the integrated Reviewer's judgment, and the
next action. The paper is complete only when the integrated Reviewer finds
that it holds and reports the corresponding result:

`done`

Do not create separate scientific, visual, language, or revision-history files,
or files declaring the paper ready.

## When another skill would help

Start with this guide. Each preliminary assessment opens only the specialist skill
for its assigned dimension. Engineer opens a repair skill only for a concrete
finding. Do not read all the sources in advance.

| When needed | Open | Use it for |
|---|---|---|
| Scientific completeness is under review | `reviewer/academic-paper-peer-review-benchmark.md` | Judge contribution, evidence, and paper value |
| A material claim or citation is disputed | `engineer/claims-against-evidence.md` or `engineer/citation-check.md` | Trace the claim to raw evidence or a primary source |
| Visual quality needs venue calibration | `engineer/paper-exemplar-pdf-learning.md` | Compare the rendered paper with strong accepted work |
| The method pipeline needs composition or visual repair | `engineer/paper-framework-figure-studio.md` | Repair the composition, native PPTX, and included vector PDF |
| An existing suitable SVG has a specific defect | `engineer/research-svg-pipeline.md` | Repair and re-export the existing source |
| PDF-only argument and language are under review | `reviewer/venue-academic-language-review.md` | Judge evidence hierarchy and prose from the rendered paper without internal context |
| Private implementation detail may have leaked | `engineer/paper-infrastructure-review.md` | Inspect the current paper for internal leakage |
| A repaired paper needs venue compilation | `engineer/venue-format-preflight.md` | Recompile under the official author kit |

These Skills support one pass or repair. They do not create another review
workflow or another review file.
