---
name: "Final Paper Review"
description: "Independently review the current paper and overwrite its single authoritative REVIEW.md."
---

# Final Paper Review

Start with:

- `paper/main.tex`;
- its rendered output;
- `paper/REVIEW.md`.

Do not load project-root `HANDOFF.md`.

## Parallel final passes

The Reviewer runs three independent read-only inspections concurrently on the
same current paper version:

1. **Scientific completeness** — follow direct claim-critical references to the
   executed code path, configuration, raw rows, evaluator, baselines, citations,
   and primary sources. Verify that the full paper supports one strong thesis.
2. **Strict visual quality** — render and inspect every page and every included
   figure and table at publication scale. Any visible overlap, clipping,
   overflow, connector penetration, wrong arrow, unreadable label, malformed
   table, misleading plot, abnormal whitespace, broken float placement, or
   inconsistent typography is a failure.
3. **Academic language** — report precise proposed changes for confident,
   accurate academic prose and coherent argument flow. Identify defensive
   qualifier boilerplate, experiment chronology, internal workflow language,
   repeated caveats, and integrity self-praise without modifying the manuscript.

Record the three combined findings only in `paper/REVIEW.md`. Do not create
three project review files.

## Repair and integrated acceptance

Engineer applies the combined scientific, visual, and language fixes and
recompiles the complete paper. The normal independent Reviewer then inspects
the repaired paper as a whole and may reject it even when all three preliminary
passes were favorable.

Overwrite `paper/REVIEW.md` with:

1. the scientific, visual, and language assessment;
2. strongest accept case;
3. reject-level issues;
4. authoritative verdict;
5. next action.

Do not create another review file or review history. Repair method, experiment,
or paper defects inside Review; never request rollback. Review is the terminal
certified stage.
