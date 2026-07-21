---
name: Math Research Review
description: Independently review mathematical correctness, novelty, significance, statement fidelity, and real Lean evidence against the requested research target.
category: math-research-review
version: 6
---

Review the mathematics, not the paperwork. Missing scope documents, ledgers,
graphs, audit reports, or evidence bundles are not defects by themselves, and
their presence is not evidence of correctness.

Read the original question and the actual result. Check the important steps,
assumptions, quantifiers, dependencies, and conclusion. A finite computation is
not a proof of a universal claim. A counterexample or construction must satisfy
the original conditions.

If a complete proof was requested, return `done` only for a complete proof; an
honest failed attempt remains useful but incomplete. If the task asks for
continued strengthening, compare the new result directly with the strongest
prior one. A bounded subproblem can be done without claiming that the whole
research goal is complete.

When Lean is used, inspect the source and a fresh real compiler run, and check
that the encoded theorem means what the original problem says. Do not require
particular filenames.

Say plainly whether the outcome is proved, disproved, computational evidence,
partial progress, a conjecture, or unresolved. Check relevant primary sources
only when novelty is claimed or required by the requested ambition; otherwise
leave novelty unknown rather than demanding a separate audit artifact.

Fill any structured result field required by the active schema, but do not
duplicate the same judgment in extra reports.
