---
name: Math Research Planning
description: Plan dynamic mathematical research inside scope, solve, and review without creating Math-specific role or lifecycle machinery.
category: math-research-planning
version: 1
---

MISSION TYPE: MATHEMATICS. Choose work from the actual mathematical structure
of the problem. Use background retrieval, examples, counterexamples,
computation, proof construction, or Lean only when useful; these are methods,
not fixed stages. Preserve the Manager-owned `research_target_level`. Known
results, finite checks, local Lean proofs, and honest failure reports are useful
evidence but cannot satisfy a publishable or doctoral target.

When formalization will reduce uncertainty, assign the generic Engineer a
bounded step that invokes `python -m argus_skill.tools.lean_check`; do not create
a Math-owned supervisor or child-task orchestrator.
