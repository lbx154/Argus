---
name: "Software Project Grounding Before Decomposition"
description: "Inspect the repository before handing software work to Planner or Engineer; identify architecture, exact analogues, verification commands, and contract risks without changing the task."
---

# Software Project Grounding Before Decomposition

## When to use

Use for every formal software repair or feature before execution begins.

## Grounding method

1. Inspect the smallest relevant project map: language/module roots, target
   package, build metadata, and repository instructions.
2. Trace the requested behavior from public entry point to implementation and
   list every unchanged caller affected by type, default, ordering, or mapping
   changes.
3. Find the closest sibling implementation or prior module that already obeys
   the repository's conventions. Treat it as an independent contract oracle,
   not code to copy blindly.
4. Identify the narrow build/test commands and any held-back-test risks:
   return type, argument order, zero/default values, complete field mappings,
   invalid input, boundary behavior, and root/platform assumptions.
5. Produce a compact grounding brief. Preserve the operator task verbatim;
   add evidence and risks, never invent requirements.

## Handoff

The brief should name relevant files, the closest analogue, exact verification
commands, and the highest-risk compatibility assumptions. For a direct task,
give it to Engineer. For staged work, give it to Planner before decomposition.

## Pitfalls

- Do not call visible tests "acceptance tests" when official tests are held back.
- Do not infer expected behavior from the patch under review.
- Do not turn repository exploration into an unbounded audit.
