---
name: Math Research Execution
description: Execute mathematical scope and solve work with honest result classification, statement fidelity, and optional real Lean compilation.
category: math-research-execution
version: 1
---

MISSION TYPE: MATHEMATICS. Dynamically choose the path that fits the problem.
Distinguish conjecture, finite or numerical evidence, natural-language proof,
formal verification, known results, and original candidates. State the limits of
every result and preserve the operator's `research_target_level`.

When Lean reduces uncertainty, first author `statement_fidelity.md`, then invoke
the generic tool:

`python -m argus_skill.tools.lean_check <lean-source> --lake --artifact-dir . --statement-fidelity statement_fidelity.md`

Preserve any descriptive source while materializing `Main.lean`,
`compile.log`, `lean_check.json`, and `statement_fidelity.md`. Never report Lean
success without a fresh real compilation, proof-hole scan, axiom audit, and
side-by-side statement audit. Lean compilation verifies only the encoded
theorem, not fidelity or novelty.
