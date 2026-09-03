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

Start with `paper/main.tex`, its rendered output, and `paper/REVIEW.md`. Do not
load project-root `HANDOFF.md` or recursively inspect historical project files.
Follow only direct claim-critical references to code, configuration, raw
results, evaluators, baselines, bibliography, figures, and primary sources.

## Work

1. Run three independent read-only passes concurrently on the same pre-repair
   paper:
   - scientific completeness and claim-to-code fidelity;
   - strict page-by-page visual quality at publication size;
   - academic language and full argument flow.
2. Combine their findings in the single `paper/REVIEW.md`.
3. Have one Engineer repair the method, evidence, manuscript, figures, tables,
   citations, and compilation as needed.
4. Recompile the complete paper.
5. Have a normal independent Reviewer reassess the repaired paper as a whole,
   including venue compliance.

All scientific, experiment, visual, and language defects are repaired inside
Review. The stage never rolls back.

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
| Academic language is under review | `reviewer/venue-academic-language-review.md` | Return precise language repairs in read-only mode |
| Private implementation detail may have leaked | `engineer/paper-infrastructure-review.md` | Inspect the current paper for internal leakage |
| A repaired paper needs venue compilation | `engineer/venue-format-preflight.md` | Recompile under the official author kit |

These Skills support one pass or repair. They do not create another review
workflow or another review file.
