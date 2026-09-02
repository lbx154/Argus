---
name: "AGENTS.md New Research Project Template"
description: "Minimal five-stage contract for a new autonomous research-paper project."
---

# New Research Project Template

Use this template for a clean research project. Do not inherit another
project's title, thesis, benchmark, method, results, or figures unless the
operator explicitly supplies them.

```markdown
# Project contract

Produce one strong, positive, thesis-driven research paper through:

Idea -> Build -> Experiment -> Paper -> Review

Stages move forward only. Repair defects in the current stage.

## Idea

For a broad publishable direction, complete twelve source-only routes, one
independent review per route, and one selector. Do not run candidate or probe
experiments while choosing. Full route material remains inside `.argus`;
project-root `HANDOFF.md` records the winner and one-line rejection reasons.

## Build

Implement the selected mechanism, real strong published baselines, the real
evaluator, and a known detectable positive control through actual entry points.
Before advancing, replace `HANDOFF.md` with `# HANDOFF — BUILD` and only the
context Experiment needs.

## Experiment

Keep each run reproducible from code, explicit configuration, command, and raw
output. Let development evidence change the method, baseline, benchmark,
controls, and next experiment. Enter Paper only when relevant wins clearly
exceed losses, headline comparisons win, and the strongest same-information
baseline is beaten.

## Paper

Write a complete compilable paper around the strongest supported contribution.
Include every intended experiment, figure, table, citation, and venue-required
section. Replace `HANDOFF.md` with `# HANDOFF — PAPER`. Final visual inspection,
academic-language polishing, and whole-paper acceptance happen only in Review.

## Review

Run scientific, strict visual, and academic-language inspections concurrently
in read-only mode. Apply their combined findings, recompile, and obtain one
integrated independent verdict in `paper/REVIEW.md`. Create no parallel review
files.

## Figure contract

Semantic geometry: every connector has the correct source, destination,
direction, port, and label; no connector penetrates an unrelated node or text.
At final publication size, figures and tables must be readable, unclipped,
non-overlapping, visually balanced, and scientifically conventional.

## Deliverables

- implementation, direct configuration, and raw results;
- `HANDOFF.md`;
- `paper/main.tex`, bibliography, figures, tables, and rendered paper;
- `paper/REVIEW.md`.

Do not create process inventories, duplicate reports, review histories, or
parallel handoff files.
```
