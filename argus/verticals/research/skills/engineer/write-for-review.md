---
name: "Write for review"
description: "How research code is written so a Reviewer who was not present can read it: a `# @component` anchor above every component entry point, `# @simplified` for deliberate departures, `# @reuses` at import sites, `# why:` on non-obvious decisions and hyperparameters, no dead or duplicated code, tests named by component. The host builds the review packet from these anchors."
---

# Write for review

The Reviewer has no tools and re-runs nothing. It sees a review packet the
host assembles from your anchors: each component's code excerpt, the host-run
test outcomes, the config changes and the files changed this round, beside
`METHOD.md`. An unanchored component is invisible to the Reviewer and is
reported `NOT_IMPLEMENTED`.

## Anchors

- `# @component <name>` on the line directly above the `def` or `class` that
  is the component's entry point. `<name>` is spelled exactly as in the
  card's `Components` table and as in the `@pytest.mark.component` marker on
  its tests. One anchor per component entry point; helpers are not anchored.
- `# @simplified <name>: <why>` directly below the component anchor when the
  implementation deliberately departs from what the route prescribes. The
  same departure is named in the card's `Notes` column.
- `# @reuses <library> <symbol>` on the import line for reused external code.
  Reused code is imported from the pinned `third_party/` clone or the
  installed package, never copied into the project.
- `# why: <reason>` on or directly above a non-obvious decision: a
  tolerance, a masking choice, an ordering, a shortcut taken.

## Configuration

Hyperparameters live in config files (`configs/*.yaml`), each chosen value
with a `# why:` comment beside it. No load-bearing defaults hidden in function
signatures; the run is reproducible from the config it names.

## Shape of the code

- No dead code, no commented-out alternatives, no second implementation of
  a component kept "for comparison": one entry point per component.
- Explicit configuration over implicit behaviour; a flag that changes the
  method is named in the config and in the card.
- Tests under `tests/spec` are named by component and kind
  (`test_<component>_knockout`, `test_<component>_differential`).
- The round summary names the anchors added or changed this round.
