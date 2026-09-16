---
name: "Method card (METHOD.md)"
description: "Write the project-root METHOD.md once: the authoritative statement of the method as the paper will claim it, component by component with the route's own words, the protocol, and what would falsify it. Read it before implementing and whenever the Reviewer or Planner asks what the method is. Status, tests, reused code, hyperparameters and history are derived by the host, not maintained by hand."
---

# Method card (METHOD.md)

`METHOD.md` at the project root is the one place that says what the method
*is*. The Engineer who implements the method writes it, from the selected
route, before writing method code; the Reviewer reads it before anything
else; the Atlas web UI shows it to human readers unchanged, next to a
derived panel the host fills in. It is a work product, like the code and the
raw outputs: the one named exception to the rule that research notes are the
sole cross-stage context and that no extra reporting files are written.

## Why it exists

A project failed when the Engineer defined the method in passing inside the
notes, implemented a simplified version, wrote tests shaped to pass, and a
read-only Reviewer certified the Engineer's account of it. Nobody could point
to a sentence saying what the method was supposed to be. The card fixes the
statement first, so every later step is a comparison against it, not a
restatement of it.

## When to write it

In the first Experiment round, before any claim-bearing run, together with
the reference clone (`engineer/delta-on-reference.md`) and the spec suite
(`engineer/executable-spec.md`). Finish these three before reporting the
first round. In the final Review, revise it before a scientific repair that
changes the method.

## How to write it

Write it once. Everything in it is about the method, not about the state of
the project.

1. Open the selected route artifact. Its path is listed under "Evidence
   considered" in `RESEARCH_NOTES.md`. Quote the route's own sentences for
   each component; do not paraphrase them into something easier.
2. Copy the body of `engineer/method_card_template.md` (everything below its
   front matter) to `METHOD.md` and fill it in order:
   - Title and a one-paragraph statement of the method as the paper will
     claim it.
   - `Components` table: `Component | The idea prescribes | Notes`. Quote the
     route in the second column. The `Notes` column is empty unless you
     deliberately simplify a component; then it says what was simplified and
     why. Spell the component name exactly as the marker on its tests will.
   - `Protocol`: datasets, baselines, seeds or repeats, metrics and the
     config files the runs use, copied from the route. Name every deviation
     on its own line with the reason.
   - `What would falsify the claim`: the observation that would make the
     paper's central claim false.
3. Tag every test under `tests/spec` with
   `@pytest.mark.component("<component>", kind="knockout"|"differential"|"invariant"|"claim"|"parity")`,
   the name spelled as in the card. The template `conftest.py` records the
   markers; the host joins them with its own run of the suite.
4. Put the reason for a chosen hyperparameter where the value is: a
   `# why: ...` comment on the same line or the line above it in the config
   file (`configs/*.yaml`). The host reads the value and the reason from
   there.

That is all you maintain. The host derives, at zero model cost, and shows in
Atlas and to the Reviewer: each component's status (proven, contradicted,
partial, untested, unchecked) from the markers and the host-run checks; the
reused code from an import scan of the project mapped to `third_party/`
clones (pinned revision, remote) and installed packages (version); the
hyperparameters from the config files with their `# why` reasons and a diff
against the previous round; the change history from git. Do not write any of
these into the card.

## Keep it current

Touch `METHOD.md` again only when the method itself changes: a component is
added or removed, a component is deliberately simplified (say so in `Notes`),
the protocol changes, or the falsifier changes. Nothing else in the project
requires a card edit: tests, code, configs and history speak for themselves
through the derived panel. A `Notes` entry naming a simplification is honest
and allowed; a component the card states plainly and the code contradicts is
the defect the Reviewer looks for first.

## What it is not

Not a plan, not a report, not a JSON ledger, and not a gate: nothing reads it
to block a stage. Roles read it and judge. The manuscript's Method section
and claims follow it; a deviation appears in the card before it appears in
the paper. Do not duplicate the card into the research notes; the notes may
point to it.
