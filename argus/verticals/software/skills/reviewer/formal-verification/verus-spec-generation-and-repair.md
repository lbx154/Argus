---
name: Verus Correctness and Completeness Review
description: Independently accept Rust module specs only with source-and-docstring fidelity, Verus implementation proofs, spec-determin-tool completeness proofs, and complete per-API artifact coverage.
---

# Verus Correctness and Completeness Review

Read the [shared workflow](../../verus-spec-generation-and-repair.md) before
reviewing a Verus specification task. Use this OWN Skill, not only the
Engineer's methodology or self-reported results.

## Inspect the real scope and semantics

Check the submodule/API inventory, dependency order, view definitions, and
source correspondence. Confirm required views preceded dependent specs and
that a different API ordering has a real justification. Do not promote a
missing constructor relation or an arbitrary observer to a complete family.

Compare every changed contract with both its implementation and docstring.
Inspect signatures, legal inputs, return branches, contents, mutation frames,
consumed iterator states, and relevant callback behavior. Check shared
dependencies and representative compositions, not only isolated declarations.

## Independently check both proof dimensions

For correctness, replay the affected source implementation proofs with the
intended Verus baseline. Verify that the relevant bodies/obligations are checked
against the current candidate, and that source-derived transformations have
their correspondence obligations discharged. Reject circular use of the target
assumption, skipped bodies, synthetic placeholders, newly trusted answers, and
zero-obligation runs. Typechecking, native tests, and client proofs do not
replace this dimension.

For completeness, run the actual `spec-determin-tool` path on the current
contracts and inspect its generated proof/query, selected output relation,
premises, and result. Reject success based on weakened comparisons,
answer-bearing inputs, false preconditions, `UNKNOWN`, or a replacement checker.
Check relevant lawful witnesses. A deterministic wrong answer is not correct;
an implementation proof alone does not show completeness.

Replay changed APIs and affected dependencies. Reuse unchanged evidence only
after establishing that its candidate, proof inputs, and baseline still match.
Do not infer either result from Engineer's prose.

## Strict implementation-change and issue review

Require an issue proposal before any implementation change, including
executable rewrites in source-derived proof copies. Independently inspect the
original/proposed implementation diff, source and docstrings, observable and
safety effects, source-to-proof correspondence, and affected proofs. Do not
approve a different implementation merely because it makes the spec provable.
Record the review decision in the issue; canonical std or Verus changes still
require explicit operator authorization.

Reproduce reported Verus limitations on the intended baseline, including
unsupported Rust syntax. Before confirming a std implementation/docstring
defect, rule out a faulty spec/view/proof or checking encoding and identify the
actual source/docstring error. A failed proof or `UNKNOWN` is not sufficient.
Unconfirmed diagnoses stay `needs-review`.

Check that required findings and change proposals appear in `issues/`, link
their affected APIs/proofs, and have honest review and filing status. A local
draft is not an upstream issue URL. A reviewed issue does not by itself pass
either proof track or repair an affected API.

## Delivery and verdict

Reconcile specs organized by submodule, the per-API Verus correctness-proof
directory, the separate completeness-proof directory, and the `issues/`
collection with its index. Every in-scope API must map to its spec, both proofs,
two explicit outcomes/replay commands, and any related issues. If no issues were
found, require an explicit statement of the reviewed scope rather than invented
reports. Status labels, missing proof files, or duplicated helper entries are
not API proof coverage.

Accept an API only when both dimensions pass for the same candidate. Module
completion additionally requires complete coverage and a usable aggregate.
Planning/view-only tasks may finish their narrower scope without certifying
dependent APIs. For unsuccessful API work, state the smallest failed obligation
and next repair, or a concrete blocker/replan reason; do not lower the gate.
Verifier changes remain separately authorized tool work.
