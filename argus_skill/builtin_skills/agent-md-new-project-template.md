---
name: AGENTS.md New Project Template
description: Neutral copy-ready AGENTS.md template for starting a new autonomous project from scratch without inheriting prior project assumptions.
category: project-agent-template
version: 1
---

## Title
AGENTS.md New Project Template

## When to use
- Use this when creating a new project/workspace and you want the daemon, planner, engineer, reviewer, critic, and scientist to share one neutral project contract.
- Use it before any implementation or paper drafting begins.

## When NOT to use
- Do not use this to continue or repair an existing project with valuable artifacts, tests, user edits, or a partially accepted direction. Use the existing-project optimization template instead.
- Do not fill it with copied claims, benchmark choices, result numbers, figures, or generated artifacts from another project.

## Copy-ready `AGENTS.md`

```markdown
# AGENTS.md

## Project contract
This is a clean-slate project. Build the project from the explicit operator goal, local evidence, and documented external sources. Do not inherit titles, claims, datasets, benchmark episodes, generators, figures, review artifacts, or result numbers from any prior project unless they are listed in **Allowed starting inputs** below with source, license/access status, and a reason they are appropriate.

## Operator goal
- Primary goal: [write the concrete deliverable in one sentence]
- Success condition: [write the observable final state, command, metric, or artifact]
- Non-goals: [write what should not be optimized or copied]
- Target audience/user: [write who will use or evaluate the result]

## Allowed starting inputs
List every starting input before using it:

| Input | Source/path/URL | License/access | How it may be used | Why it is appropriate |
| --- | --- | --- | --- | --- |
| [input] | [source] | [status] | [allowed use] | [rationale] |

If an input is not listed here, treat it as unavailable until documented.

## Role model
- Planner: decomposes the goal into bounded tasks with acceptance criteria and dependencies.
- Engineer: implements the next bounded task, changes source artifacts rather than patching generated outputs, and runs the relevant validation.
- Reviewer: checks correctness, evidence, freshness, and whether the acceptance criteria are genuinely satisfied.
- Critic: looks for hidden failure modes, overclaiming, shortcuts, and validator-gaming.
- Scientist: distills reusable lessons only after a task succeeds; write guidance for a smaller engineer model, with concrete gates and anti-patterns.

## Operating rules
1. Read this file before each new mission or round.
2. Resolve instruction conflicts in this order: explicit operator instruction, this file, repository documentation, then inferred conventions.
3. Prefer small bounded tasks with clear acceptance criteria over broad polishing.
4. Preserve user edits and unrelated work. Do not revert files you did not intentionally change.
5. Do not claim success from plans, intentions, stale artifacts, or unrun commands.
6. If a file is generated, edit the generator/source and regenerate the artifact unless the operator explicitly requests a direct patch.
7. Keep source, generated artifacts, manifests, reviews, and validation reports synchronized.
8. Record meaningful decisions and evidence in project files, not in transient chat.

## Discovery workflow
1. Inventory the repository/workspace and identify the canonical source files, generated artifacts, tests, validators, and logs.
2. Identify what is unknown. If the unknown changes the implementation approach, ask the operator; otherwise make a conservative assumption and document it.
3. Create or update a project state artifact such as `research/PROJECT_STATE.md`, `docs/PROJECT_STATE.md`, or the repository's existing equivalent.
4. Before writing final claims, create an evidence map from each claim to source data, code, logs, or citations.

## Implementation workflow
1. Define the next bounded objective and acceptance criteria.
2. Read the relevant files fully before editing.
3. Implement the minimal complete change that satisfies the objective.
4. Run existing tests, linters, builds, validators, or smoke checks that cover the changed behavior.
5. If validation fails, fix the root cause rather than weakening the check.
6. Update documentation only when it is directly affected by the change.

## Research/paper workflow, if applicable
1. Literature and benchmark selection are part of the research contribution. Survey credible recent and classic sources before choosing a thesis or benchmark.
2. Use multiple benchmark/data sources when possible. Do not duplicate, relabel, or lightly paraphrase the same examples to inflate scale.
3. Keep benchmark provenance: source, version/date, license/access, filtering/sampling rules, deduplication method, oracle-leakage checks, and reason for inclusion/rejection.
4. Treat pilot evidence as pilot evidence. Do not present small or synthetic-only experiments as a complete paper result.
5. Keep every numeric claim tied to generated artifacts or raw result files.
6. Write reader-facing academic prose. Do not expose validator names, evidence-span bookkeeping, raw paths, or internal review mechanics in the abstract/body.
7. If the project requires generated conceptual figures, use the specified generation tool and preserve prompt, provenance, raster output, review, and SHA/dimension metadata. Do not replace a required generated figure with a local redraw.

## Forbidden shortcuts
- Do not fake experiments, citations, provenance, tests, reviews, or validation outputs.
- Do not edit reports to say "ready" while known blockers remain.
- Do not satisfy validators by adding boilerplate that makes the actual artifact worse.
- Do not copy a previous project and rename variables to make it look new.
- Do not silently ignore failed commands or missing artifacts.

## Completion contract
A task is complete only when:
- the requested behavior/artifact exists,
- source and generated artifacts are synchronized,
- relevant validation has passed or remaining failures are explicitly unrelated,
- known limitations are documented without pretending they are solved,
- the handoff states what changed, what passed, and what remains.
```

## Generality check
This template must stay project-neutral. It may describe process and gates, but it must not contain a specific project title, benchmark name, result number, figure name, or repository path.

## Coverage check
Before using the template, fill all bracketed placeholders and delete any section that does not apply.
