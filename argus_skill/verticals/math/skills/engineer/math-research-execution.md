---
name: Math Research Execution
description: Execute mathematical scope and solve work with honest result classification, statement fidelity, and optional real Lean compilation.
category: math-research-execution
version: 6
---

Do the mathematics in the form that best fits the problem. Distinguish a proof,
counterexample, construction, finite experiment, formal verification, known
result, and conjecture; do not describe one as another.

Start from the exact question and the requested bar. If a complete proof is
required, useful failures and computations do not make the mission complete.
Keep a short note about a failed route in the existing `CHECKPOINT.md` when it
will help the next attempt, then change the mathematical approach.

Use ordinary working files suited to the task. Do not create process-only
planning, ledger, graph, audit, status, or evidence-packet files merely to
satisfy the workflow. The theorem, proof, counterexample, code, or formal source
is the evidence.

When continuing earlier work, compare the new result with the strongest prior
result that matters. Explain the mathematical improvement directly; no special
tracking file is required.

If Lean is useful, keep the formal source and show a fresh real compiler run with
no proof holes. The generic `python -m argus_skill.tools.lean_check` helper is
available, but no fixed bundle of output filenames is required. Compilation
checks the encoded theorem, so also compare that theorem with the original
statement.

Before investing heavily in a new conjecture, a small counterexample search may
be useful. For a construction, check that the object satisfies every condition.
Use literature only when a known result matters or when claiming novelty. These
are mathematical choices, not boxes that must all be checked.
