---
name: "Digital Circuit Guidance Promotion Review"
description: "Promote only reusable, independently supported RTL lessons into the skill library while blocking benchmark-answer memorization."
---

# Digital Circuit Guidance Promotion Review

## Review protocol

1. Require support from at least two independent tasks, or one task plus an
   authoritative public hardware reference.
2. Rewrite the lesson as a generic detector, invariant, repair strategy, or
   verification gate. Remove task IDs, hidden values, exact expected traces,
   and benchmark-specific implementation details.
3. Record failure taxonomy, evidence paths, applicability, counterexamples, and
   the benchmark phase after which the skill becomes eligible.
4. Freeze the promoted skill before the next evaluation phase; never update a
   skill mid-phase and then include later tasks from that phase in the same
   headline comparison.
5. Require a held-out or later-phase ablation showing that the skill improves
   correctness without increasing false repairs.
6. Keep version history and make every promotion reversible.

## Reject promotion when

- the lesson is supported only by official hidden-oracle behavior;
- it encodes a benchmark task's ports, constants, state sequence, or answer;
- it was inferred from missing public context;
- it improves only the training/evaluation task used to author it;
- provenance, license, or evidence is missing.
