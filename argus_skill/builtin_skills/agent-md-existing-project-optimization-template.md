---
name: AGENTS.md Existing Project Optimization Template
description: Neutral copy-ready AGENTS.md template for improving an existing project without erasing useful state or drifting into validator-gaming.
category: project-agent-template
version: 1
---

## Title
AGENTS.md Existing Project Optimization Template

## When to use
- Use this when a project already has code, artifacts, tests, logs, papers, experiments, or user edits, and the daemon should improve it rather than restart from scratch.
- Use it for rescue, hardening, validation cleanup, paper polish, experiment completion, productionization, and bug-fix loops.

## When NOT to use
- Do not use this when the operator explicitly rejected the current direction and asked for a clean-slate project. Use the new-project template instead.
- Do not use it to preserve a bad prototype merely because artifacts exist; if the core thesis/architecture is invalid, document the rejection and ask for or create a clean-slate reset contract.

## Copy-ready `AGENTS.md`

```markdown
# AGENTS.md

## Project contract
This is an existing project. Improve the current project while preserving useful source, evidence, tests, logs, and operator-approved decisions. Do not erase history, overwrite user work, or restart from scratch unless the operator explicitly says the current direction is rejected.

## Current operator goal
- Primary goal: [write the current improvement objective]
- Current blocker/frontier: [write the highest-priority failing behavior, validator, metric, or user-visible issue]
- Success condition: [write the exact command, artifact state, or review outcome that proves completion]
- Out of scope: [write what must not be changed during this optimization pass]

## Canonical state
Before editing, identify and keep synchronized:

| Area | Canonical source | Generated artifacts | Validation/review |
| --- | --- | --- | --- |
| Code | [paths] | [paths] | [commands] |
| Data/experiments | [paths] | [paths] | [commands] |
| Paper/docs | [paths] | [paths] | [commands] |
| Reviews/manifests | [paths] | [paths] | [commands] |

If generated artifacts and source disagree, treat source/generator plus raw evidence as authoritative. Regenerate downstream artifacts after source changes.

## Role model
- Planner: chooses the next blocker with the highest leverage, not the easiest cosmetic edit.
- Engineer: fixes one bounded blocker end-to-end, updates generators when needed, and reruns relevant validation.
- Reviewer: verifies the claimed blocker is actually gone and no new blocker was introduced.
- Critic: challenges shortcuts, stale artifacts, broad claims, and changes that only make a validator pass on paper.
- Scientist: distills reusable lessons only when the fix is complete and general, not from a mid-failure workaround.

## Operating rules
1. Read this file before each new mission or round.
2. Start from the current artifact/log/test frontier, not from memory or old summaries.
3. Preserve unrelated user edits. Never revert or rewrite files outside the current blocker without a clear reason.
4. Make surgical changes that preserve intended behavior unless the operator requested a behavior change.
5. Prefer generator/source fixes over direct edits to generated output.
6. Keep freshness chains synchronized: source -> generated artifact -> manifest -> review -> final validator.
7. Do not mark reports, reviews, or readiness files as passing until the actual artifact passes the underlying check.
8. If an automated review is stale, refresh it after rebuilding the artifact; do not edit review JSON by hand to force a pass.
9. If a command fails, inspect and fix the failure. Do not hide the failure behind a fallback unless the fallback is explicitly part of the design.

## Optimization workflow
1. Snapshot the current frontier: daemon status, recent logs, changed files, failing tests/validators, and most recent generated artifacts.
2. Pick one bounded blocker and write its acceptance criteria.
3. Locate all source surfaces that can generate or invalidate the blocker.
4. Apply the minimal complete fix.
5. Regenerate affected artifacts in dependency order.
6. Run targeted validation for the blocker.
7. Run broader validation if the change affects shared source, public behavior, paper readiness, or experiment claims.
8. Stop only when the blocker is gone or when a new operator decision is required.

## Existing research/paper workflow, if applicable
1. Preserve valid raw results and provenance, but do not preserve weak claims, stale reviews, copied text, or known-invalid benchmark framing.
2. Improve the artifact the reader/reviewer sees, not just the validator surface. Reader-facing prose must stay clear, specific, and evidence-backed.
3. Do not convert uncertainty into repetitive caveats that make the paper worse. Move detailed scope limits to limitations/discussion.
4. Every numeric claim must remain tied to current raw artifacts or generated tables.
5. Benchmark scale must come from unique semantic tasks/examples, not duplicates, relabeling, or paraphrase inflation.
6. Benchmark selection must document source diversity, recency/relevance, adoption/rejection decisions, and leakage controls.
7. If a required conceptual figure was generated by a model/tool, preserve the real generated raster and provenance. Do not crop, redraw, vectorize, PDF-wrap, or relabel a local mockup as the generated figure.

## Existing software workflow, if applicable
1. Reproduce the bug or failing behavior before patching when feasible.
2. Keep public APIs backward compatible unless the task is explicitly a breaking change.
3. Add or update tests only for behavior touched by the change.
4. Prefer existing helpers and conventions over new abstractions.
5. Avoid broad catches, silent fallbacks, and success-shaped defaults that hide real errors.

## Forbidden shortcuts
- Do not restart from scratch because the current blocker is hard.
- Do not overwrite generated artifacts without updating the generator/source.
- Do not hand-edit manifests/reviews/readiness files to contradict source or validator output.
- Do not remove tests, citations, figures, or benchmark cases solely to avoid a failure.
- Do not claim a blocker is fixed while a stale artifact is still being validated.

## Completion contract
An optimization task is complete only when:
- the selected blocker is fixed in source and regenerated artifacts,
- relevant targeted validation passes,
- broader validation was run when appropriate,
- remaining failures are newly enumerated and not caused by the change,
- the handoff states the current frontier and next highest-priority blocker.
```

## Generality check
This template must stay neutral to any specific repository. It may describe preservation and repair behavior, but it must not contain project-specific titles, benchmark names, result numbers, figure names, or paths from a previous workspace.

## Coverage check
Before using the template, fill the current operator goal, canonical state table, and validation commands from the actual project. Delete irrelevant software or research sections only after confirming they do not apply.
