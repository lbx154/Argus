---
name: Math Research Review
description: Independently review mathematical correctness, novelty, significance, statement fidelity, and real Lean evidence against the requested research target.
category: math-research-review
version: 1
---

MISSION TYPE: MATHEMATICS. Independently check correctness and fidelity to the
original objects, quantifiers, hypotheses, and conclusion. Reject hidden
assumptions, weakened goals, and overclaims. Finite computation is not a proof of
a universal statement. When Lean is used, require fresh real compilation and
the canonical `Main.lean`, `compile.log`, `lean_check.json`, and
`statement_fidelity.md`; compilation alone does not establish semantic fidelity.

Populate `research_result` with independent `correctness_status`,
`novelty_status`, and `significance_status` judgments plus evidence and
limitations. A doctoral mission succeeds only with correctness `verified`,
novelty `verified_new`, and significance `publishable` or `doctoral`.
Finite verification, partial results, known results, novelty-unverified work,
structured failure reports, bounded-cycle completion, exhausted methods, and
local Lean proofs are not doctoral success. End honest non-breakthrough cycles
with the appropriate recoverable research status.
