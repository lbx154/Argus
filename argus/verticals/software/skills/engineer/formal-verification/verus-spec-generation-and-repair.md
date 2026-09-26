---
name: "Verus API Specification and Proof Repair"
description: "Implement view-first API specifications from source and docstrings, prove source correctness with Verus, check completeness with spec-determin-tool, and repair until both proof tracks pass."
---

# Verus API Specification and Proof Repair

Read the [shared workflow](../../verus-spec-generation-and-repair.md), Manager's
scope, and Planner's submodule/dependency map before writing API specifications.
If a direct task lacks the map, establish the necessary submodule and view
dependencies first; do not skip them.

## Per-submodule and per-API loop

1. Reuse an adequate existing view, or define the needed view and identify its
   correspondence obligations before dependent API contracts. Discharge
   constructor-dependent obligations with the source implementation proofs
   before acceptance. Follow the planned bottom-up order or its documented
   alternative.
2. Read both source code and docstrings for each API, including delegated calls
   and edge cases. Preserve signatures and legal inputs. Write the contract in
   the module's spec files using the established views and existing borrowing
   support, including required `old`/`final` writeback and frames.
3. Build the per-API source implementation proof and run Verus on the actual
   relevant body/obligations. Retain source mappings for any faithful
   translation; executable rewrites follow the issue/review policy below.
   Calling the external API under its assumed candidate contract is only a
   client proof, not this implementation proof.
4. Run the actual `spec-determin-tool` entry point on the same current contract.
   Keep its per-API completeness proof/harness, comparison, query inputs, result,
   and replay command. Do not replace the tool or weaken the comparison.
5. If either dimension fails or is inconclusive, use that feedback to repair the
   spec, view, faithful source proof, or checker encoding, then rerun both
   affected tracks. Shared-view changes also reopen dependent APIs.
   Neither `UNKNOWN` nor a zero-obligation run is a pass.
6. Continue until both dimensions pass for the current candidate. If a concrete
   limitation prevents closure, preserve the attempts and report the blocker;
   do not mark that API complete or silently change the Verus trusted basis.

## Raise issues instead of silently changing implementations

Raise an issue in `issues/` before changing an implementation, including
executable rewrites in a source-derived proof copy or a verifier workaround.
Keep any exploratory patch an isolated proposal, with the original/proposed
diff and source correspondence, until strict independent review and required
authorization. A passing proof of rewritten code does not certify the original
implementation. Ordinary ghost annotations or lemmas are not executable edits.

For unsupported Rust syntax or another demonstrated Verus limitation, retain a
minimal baseline reproduction and diagnostic and raise a `verus-limitation`
issue. For suspected std implementation/docstring defects, first investigate
spec/view/proof mistakes and submit the finding for independent review;
classify it as a confirmed defect only after that review. `UNKNOWN` alone is not
evidence of an upstream bug.

Follow Manager's issue-filing authorization, recording a real URL only after
external filing succeeds. Unfiled drafts remain explicit. Link failed proofs
and affected APIs, deduplicate a shared root cause, and keep unresolved APIs
incomplete rather than patching source or docs just to get a pass.

## Produce the four artifact collections

Maintain `specs/<submodule>/` files following the module map, shared views where
needed, and a buildable module aggregate; do not mix unrelated APIs into a flat
candidate file. For every in-scope API, write identifiable entries under
`proofs/correctness/` and `proofs/completeness/`, using the shared layout or an
explicitly mapped repository-native equivalent. Include `issues/INDEX.md` and
the discovered issue/proposal reports; record the reviewed scope if none exist.

Keep a compact index tying each API and source/docstring location to its spec,
views, two proof paths, commands, outcomes, and associated issue IDs. Shared proof
helpers do not replace per-API proof entries; blocked APIs retain their failing
attempts and issue status.
Typecheck the aggregate without duplicating existing vstd declarations.

## Handoff

Provide the literal contract changes and the two current proof results to
Reviewer. Supplemental clients, native tests, and negative controls can expose
semantic mistakes, but cannot stand in for either required dimension.
Run affected checks first, reuse unchanged builds, and retain precise diagnostics
instead of repeatedly running an unchanged broad suite. Do not claim module
completion from a bounded submodule result.
