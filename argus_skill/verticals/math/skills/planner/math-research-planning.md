---
name: Math Research Planning
description: Plan dynamic mathematical research inside scope, solve, and review without creating Math-specific role or lifecycle machinery.
category: math-research-planning
version: 4
---

MISSION TYPE: MATHEMATICS. Choose work from the actual mathematical structure
of the problem. Use background retrieval, examples, counterexamples,
computation, proof construction, or Lean only when useful; these are methods,
not fixed stages. Preserve the Manager-owned `research_target_level`. Known
results, finite checks, local Lean proofs, and honest failure reports are useful
evidence but cannot satisfy a publishable or doctoral target.

When the Manager objective names a hard theorem-proof success criterion, treat
it as a mission-level acceptance contract, not merely a project-level aspiration.
Every solve mission must require a precisely quantified theorem/lemma and a
complete self-contained proof accepted by an independent Reviewer. Literature,
enumeration, SAT/CP, witness search, and finite verification may be internal
discovery or checking steps, but the mission must not be accepted with
"feasibility evidence only", another bounded prefix, or any other fallback that
the operator explicitly said does not count. A failed proof attempt remains
failed/unresolved and triggers a genuinely different proof strategy next cycle.

When formalization will reduce uncertainty, assign the generic Engineer a
bounded step that invokes `python -m argus_skill.tools.lean_check`; do not create
a Math-owned supervisor or child-task orchestrator.

When solve work creates or materially refines a theorem, operator, proof
mechanism, obstruction certificate, or asymptotic route and no current
mechanism-level overlap audit exists, schedule a SEPARATE short DAG node before
further polishing or final review. It should read the mechanism artifact, run
exact-query and synonym searches, inspect the closest recent primary sources,
backward/forward citations, and foundational adjacent-field terminology, then
write `research/MECHANISM_OVERLAP_AUDIT.md`. Do not impose this literature cost
on routine derivations where the trigger did not fire.

Use these AI4M patterns only when their trigger fits:
- **Counterexample-guided refinement:** before proving a new candidate lemma or
  stronger conjecture, route the cheapest bounded falsification node; refine the
  statement from any witness.
- **Enumerate→Conjecture→Prove:** for construction/existential-answer problems,
  separate bounded example generation, canonical witness conjecture,
  admissibility checking, and proof. Do not reduce construction to proving a
  supplied answer.
- **Relational premise map:** when many prior lemmas matter, retrieve a compact
  dependency graph of known premises and missing bridge lemmas instead of flat
  document retrieval.
- **Semantic round trip:** when formalization is useful, require informal
  statement → formal statement → back-translation/fidelity audit before proof
  search. Lean success cannot repair a wrong statement.

Generate multiple proof branches only after uncertainty or a failed mechanism
justifies the cost; use a cheap verifier to prune them before deep search.
