---
name: "Implementation brief"
description: "The template every Experiment implementation task follows: the Planner writes it as TASK_OBJECTIVE, the Engineer checks it is complete before writing code. Claim verbatim from METHOD.md, the components this task builds with their entry points, interfaces, the tests that must pass, data and scale as the route states them, commands, environment prerequisites, an executable definition of done, and what is out of scope."
---

# Implementation brief

One task is one brief. A Planner fills every heading below into the task
objective; an Engineer reads it before code and completes any missing
heading from `METHOD.md` and the selected route, naming the completion in
the round summary. Nothing here is a gate: an incomplete brief is finished,
not refused. A brief never narrows the claim, the datasets or the scale to
what is convenient; a task re-issued after a failure carries the same brief
with the same acceptance, verbatim.

## Claim

The statement from `METHOD.md`, quoted verbatim. Not paraphrased, not
scoped down.

## Components to implement this task

Names exactly as in the card's `Components` table. For each: the entry
point to create or change as `path/to/file.py:Symbol`, and the equations or
route section it realizes.

## Interfaces

Function or class signatures, or the CLI, that the component exposes and
that the tests and later tasks will call.

## Tests that must pass

The `tests/spec` ids to write or make green, each marked
`@pytest.mark.component("<name>")` with the component it exercises: oracle,
differential, knockout, claim-shaped or parity.

## Data and scale

Datasets, splits, sizes, seeds and repeats exactly as the route's protocol
states them. "e.g." is not a dataset list.

## Commands

Environment activation, the run command with its config file, and the
evaluation command, as they will be executed.

## Environment prerequisites

The project venv and pinned dependencies, the `third_party/` reference
clone at its pinned revision, data present at its path, GPU or accelerator
mask, any credentials the operator has supplied.

## Definition of done

Executable checks and the numbers to report: which tests are green, which
command produced which raw output, which comparison at which scale. No
adjectives ("robust", "reasonable", "works well").

## Out of scope

What this task deliberately leaves to another brief, so the Engineer does
not spend the round on it or quietly fold it in.
