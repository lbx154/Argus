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

Apply "Plan the manuscript length" in `research-paper-playbook.md` to a full
paper. Compare its rendered body extent, under the exact track's counting
rules, with the writing target. Judge the depth of principle-level analysis:
does the reader understand why the mechanism follows, what the assumptions
imply, and why the design choices differ from alternatives? Request expansion
of terse reasoning, derivations, or tradeoffs. Experiments should support this argument, not turn the
paper into an experiment report. Put actionable expansion requests in the
existing `paper/REVIEW.md`. Respect an explicit short-paper or partial-edit request.

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

For a full `final_submission` mission, Engineer owns the substantive repairs
and high-impact improvements requested by the final Reviewer, including a
decisive baseline, control, method test, or explanatory analysis when needed.
This is full conference peer review of the contribution, novelty, methods,
experimental design, results, and conclusions, alongside presentation. Engineer
directly runs the needed scientific work in the current Review mission, revises
the paper, and returns for independent re-review. Do not roll back to Idea,
Experiment, or Paper, or turn a normal scientific repair into a request to
restart the earlier workflow.
Use the general mission operation for that scope. The narrower `narrative_edit`
operation below applies to a prose-only repair; it must not prevent an
explicitly authorized scientific improvement in a full final review.

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
`engineer/paper-framework-figure-studio.md`: default Method D (image blueprint
and editable PPT Master reconstruction), with Method B direct native PPT design
as the fallback when the image interface is unavailable. Keep the framework in
editable PPT; ECharts can supply actual data components. Preserve the academic
palette and precise mathematical typography.
Regenerate the included PDF. A successful
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

At this final stage, completion also requires an explicit independent
recommendation for the currently selected venue. Explain it naturally in the
operator's language, with the supporting evidence and remaining weaknesses.
No JSON, fixed fields, decision footer, or prescribed review template is required.
Only a clearly supported weak accept or higher with no reject-level issue may
pass. Borderline, rejection, uncertainty, missing evidence, or a mismatched venue
continues Review. The host enforces the threshold and binds the judgment to the
actual paper and figure bytes; a previous approval or successful compile is
insufficient.

There is no quality-revision count ceiling. Keep revising in the current stage
until the threshold is met, with explicit operator stops and resource/backend
failures handled separately. Do not inflate the recommendation to end a long
run, lower the venue, or hide adverse evidence. Where the contribution or
evidence is too weak, give Engineer substantive scientific repairs and decisive
experiments to execute directly within this final Review.

## Constructive feedback and improvement after final review

Help Engineer build the strongest paper the evidence can support. Start each
round with specific verified strengths and progress, explain why those results
matter, and close resolved findings. Be encouraging and respectful without
generic praise, personal criticism, invented novelty, or inflated ratings.

Prioritize feasible improvements by scientific value. For each item, identify
the opportunity, a concrete change, and a decisive validation with appropriate
controls and a success criterion. Offer creative hypotheses, method alternatives,
or analyses when they could strengthen the contribution; identify them as
untested and start with the cheapest informative test. Preserve adverse results.
Distinguish acceptance blockers, actionable high-impact improvements, and
speculative future-work ideas.

The final review must lead to actual changes when there are feasible,
high-impact improvements left, even if the current paper already merits weak
accept. Explain those opportunities and ask Engineer to revise before completion.
Engineer implements the feedback, records the changes and validation in the
existing `CHECKPOINT.md`, and returns for independent re-review. A failed proposed
hypothesis can be resolved by evidence and a supported alternative; agreement
with Reviewer is not a scientific result. Reviewer checks the actual paper and
experiments, recognizes the progress, and closes resolved findings.

Aim toward strong acceptance and best-paper quality. Clear weak accept is still
the minimum, and speculative future-work ideas do not hold completion. Do not
invent endless experiments, repeat resolved objections, or demand arbitrary
rewrites merely to sustain the loop. Use ordinary prose throughout the review;
the host handles internal routing and preserves the complete feedback.

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
| The method pipeline needs composition or visual repair | `engineer/paper-framework-figure-studio.md` | Repair the canonical composition and included vector PDF; inspect any delivered PPTX separately |
| PDF-only argument and language are under review | `reviewer/venue-academic-language-review.md` | Judge evidence hierarchy and prose from the rendered paper without internal context |
| Private implementation detail may have leaked | `engineer/paper-infrastructure-review.md` | Inspect the current paper for internal leakage |
| A repaired paper needs venue compilation | `engineer/venue-format-preflight.md` | Recompile under the official author kit |

These Skills support one pass or repair. They do not create another review
workflow or another review file.
