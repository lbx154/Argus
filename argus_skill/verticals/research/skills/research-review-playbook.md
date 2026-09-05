---
name: "Research Review Playbook"
description: "The single authoritative playbook for final scientific, visual, language, and whole-paper acceptance."
---

# Research Review Playbook

## Outcome

Independently decide whether the current complete paper is scientifically sound,
publication-ready, and worth accepting. Judge it against strong accepted and
best-paper-level work in the selected venue, not against mere checklist
compliance.

## Inputs

Input access depends on the operation. The fresh-context Narrative Editor reads
the current manuscript, `HANDOFF.md` evidence roles, and drafting contract, but
not `paper/REVIEW.md` or review history. Scientific loss review reads immutable
before/after source and PDF snapshots plus a direct claim-critical source only
for a concrete dispute. The cold reader receives an isolated workspace
containing only `paper/main.pdf`. The integrated Reviewer may start with
`paper/main.tex`, its rendered output, and `paper/REVIEW.md`, then follow direct
claim-critical references without recursively inspecting history.

## Work

1. Preserve the pre-edit manuscript source closure and rendered PDF in internal
   mission state; do not use Git as the scientific baseline.
2. Run `narrative_edit` as a fresh-context Engineer operation. It selects and
   packages evidence for each section while preserving exact facts, complete
   coverage, the five-sentence/170-word abstract, and numerical captions.
3. After the edit, run three independent read-only passes concurrently:
   - `science_loss_check` compares before/after scientific completeness,
     meaning, and carriers;
   - strict page-by-page visual quality inspects the current rendered paper;
   - `cold_read` judges argument hierarchy and academic language from the
     isolated rendered PDF only.
4. Give these internal findings to the normal integrated Reviewer. Only that
   Reviewer controls the round and overwrites `paper/REVIEW.md`; preliminary
   passes create no project-visible report or history. Semantic-loss and cold-
   read findings begin in shadow mode: until calibration explicitly enables
   enforcement, they may guide or corroborate an existing review criterion but
   cannot be the sole reason to block.
5. Have the Engineer resolve any scientific/readability conflict, recompile,
   and repeat the post-edit passes before integrated certification.

All scientific, experiment, visual, and language defects are repaired inside
Review. The stage never rolls back.

For the method pipeline, compare the actual drawing against the executed code
and manuscript: labels, branches, training/inference arrows and the highlighted
mechanism must agree. Inspect its compact horizontal, staggered layout and Times
New Roman at the included publication size. Inspect the existing PDF; do not
invoke the drawing component merely because Review started. Default placement
is after Introduction, preferably on page 2 or 3; fix float placement in LaTeX
without redrawing. Reject surplus whitespace, unreadable
labels, clipping and connector collisions; repair the editable SVG with
`engineer/research-svg-pipeline.md` and regenerate the included PDF. A successful
render or font check alone is not visual acceptance.

Reject a paper that is technically compliant but unimportant, timid, visually
unfinished, or organized around caveats instead of contribution. Do not demand
defensive qualifications that the evidence does not require.

Reject a manuscript that reads as an experiment report: run chronology,
implementation diary, tables without argumentative purpose, or a Results
section that never establishes the central claim. Require one explicit thesis
and a section-by-section argument in which each experiment answers a necessary
scientific question.

## Completion

`paper/REVIEW.md` contains `Scientific:`, `Visual:`, and `Language:` assessments,
the strongest accept case, reject-level issues, authoritative verdict, and next
action. Only an integrated `done` verdict completes the paper.

Do not create separate scientific, visual, language, revision-history, or
certification files.

## Progressive disclosure

Start with this Playbook. Each preliminary pass opens only the specialist Skill
for its assigned dimension. Engineer opens a repair Skill only for a concrete
finding. Do not preload the table.

| When needed | Open | Use it for |
|---|---|---|
| Scientific completeness is under review | `reviewer/academic-paper-peer-review-benchmark.md` | Judge contribution, evidence, and paper value |
| A material claim or citation is disputed | `engineer/claims-evidence-audit.md` or `engineer/citation-audit.md` | Trace the claim to raw evidence or a primary source |
| Visual quality needs venue calibration | `engineer/paper-exemplar-pdf-learning.md` | Compare the rendered paper with strong accepted work |
| The method pipeline needs repair | `engineer/research-svg-pipeline.md` | Repair SVG fidelity, compact geometry, typography and the included PDF |
| PDF-only argument and language are under review | `reviewer/venue-academic-language-review.md` | Judge evidence hierarchy and prose from the rendered paper without internal context |
| Private implementation detail may have leaked | `engineer/paper-infrastructure-review.md` | Inspect the current paper for internal leakage |
| A repaired paper needs venue compilation | `engineer/venue-format-preflight.md` | Recompile under the official author kit |

These Skills support one pass or repair. They do not create another review
workflow or another review file.
