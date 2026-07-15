---
name: Math Research Execution
description: Execute mathematical scope and solve work with honest result classification, statement fidelity, and optional real Lean compilation.
category: math-research-execution
version: 3
---

MISSION TYPE: MATHEMATICS. Dynamically choose the path that fits the problem.
Distinguish conjecture, finite or numerical evidence, natural-language proof,
formal verification, known results, and original candidates. State the limits of
every result and preserve the operator's `research_target_level`.

When `research/PIPELINE_STATE.json` says the current stage is `scope`, use an
artifact-first protocol:

1. Read any existing `research/SCOPE.md` and the curated working-memory
   checkpoint before doing new discovery. Once the precise problem statement,
   objects, domains, and quantifiers are established, immediately create
   `research/SCOPE.md` with `<!-- status: incomplete -->`. This first durable
   checkpoint must happen before further literature or source verification.
2. Continue through small, completed writes. Add the verified literature status,
   known results, candidate counterexamples, research boundary, and current solve
   subgoal in separate tool calls. Never defer the whole document to one large
   late `apply_patch`. Use
   `python -m argus_skill.tools.atomic_artifact write research/SCOPE.md` for the
   first stdin chunk and the same command with `append` for each later chunk.
   Each command performs file `fsync`, atomic replacement, and directory `fsync`
   where supported; treat its successful exit as a recovery boundary before
   starting the next evidence batch.
3. On a resumed round, continue from the sections already on disk and the
   reviewer-authored checkpoint. Do not repeat literature or source verification
   that already has a checkable citation or is recorded as verified in the
   checkpoint, unless new evidence creates a concrete contradiction.
4. If a tool or turn is interrupted, leave the partial artifact in place with
   `status: incomplete` and name its completed sections and evidence in the
   handoff. Change the marker to `status: complete` only after the scope
   checklist is fully addressed.

When `research/PIPELINE_STATE.json` says the current stage is `solve`, use the
same artifact-first discipline:

1. Read `research/SCOPE.md`, the existing `research/SOLVE.md` and
   `research/CLAIM_LEDGER.md` if present, and the curated checkpoint.
   Atomically create any missing `research/SOLVE.md` and
   `research/CLAIM_LEDGER.md` with `<!-- status: incomplete -->` before any new
   literature retrieval, subagent dispatch, broad planning, or long computation.
   Seed them with the verified prior facts, the active solve route, open claims,
   and explicit limitations already known from the checkpoint.
2. Use `python -m argus_skill.tools.atomic_artifact write <path>` for the first
   stdin chunk and `append <path>` for each later section. Persist source audits,
   mathematical derivations, bounded computations, and claim-status changes as
   separate recovery boundaries. Do not wait for a complete proof, counterexample,
   or literature survey before writing the first reviewable solve increment.
3. On resumed rounds, continue the existing artifacts and reuse citations,
   theorem statements, failed routes, and computations already verified on disk
   or in the checkpoint. Repeat them only when a concrete contradiction requires
   re-audit.
4. Keep both artifacts marked incomplete across interruption. Mark them complete
   only when their claims, evidence, and limitations are internally consistent
   enough for independent solve-stage review.

When Lean reduces uncertainty, first author `statement_fidelity.md`, then invoke
the generic tool:

`python -m argus_skill.tools.lean_check <lean-source> --lake --artifact-dir . --statement-fidelity statement_fidelity.md`

Preserve any descriptive source while materializing `Main.lean`,
`compile.log`, `lean_check.json`, and `statement_fidelity.md`. Never report Lean
success without a fresh real compilation, proof-hole scan, axiom audit, and
side-by-side statement audit. Lean compilation verifies only the encoded
theorem, not fidelity or novelty.
