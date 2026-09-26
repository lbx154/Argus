---
name: "Verus Specification Scope and Delivery"
description: "Scope a Verus module-specification mission with submodule analysis, view-first dependencies, mandatory correctness and completeness feedback, and complete per-API proof deliverables."
---

# Verus Specification Scope and Delivery

For Verus module specification generation or repair, read the
[shared workflow](../../verus-spec-generation-and-repair.md) before setting
the mission. This Skill is Manager-owned, including front-door/SELF scoping;
it does not require the optional generic Manager grounding pass.

## Scope and handoff

1. Identify the requested module, intended Rust source and docstrings, existing
   vstd contracts, output location, and fixed Verus baseline. Preserve the
   requested public API scope; expose ambiguities instead of silently excluding
   difficult APIs or changing signatures.
2. Require initial submodule and dependency analysis before API generation.
   Delegate the detailed map to Planner rather than duplicating its repository
   exploration. Each submodule must state whether it reuses an existing view or
   needs a new view before its API specs.
3. Require bottom-up dependency ordering, or an explicitly justified alternative.
   Views and their correspondence obligations must precede dependent contracts.
   Keep source code and docstrings as joint semantic inputs throughout repair.
4. Establish both acceptance dimensions: source implementation correctness
   verified by Verus, and completeness checked through `spec-determin-tool`.
   Have Planner identify the actual project commands; do not substitute client
   proofs, typechecking, source review, or a different checker.
5. Carry four deliverables into the handoff: specs organized by submodule, a
   correctness-proof directory with per-API entries, a separate completeness-proof
   directory, and `issues/` with an index of findings/proposals and their status.
   Require a compact coverage index linking each API's artifacts, two proof
   results, and any associated issues.
6. Establish the issue-reporting destination and external-filing authorization.
   Require an issue proposal and strict independent review before any
   implementation change; review alone does not authorize canonical Rust/std
   or Verus changes. Preserve the intended source until an explicit change is
   approved.

## Completion and escalation

Keep unsuccessful APIs in the repair loop with explicit failures or blockers.
Do not declare the module complete from a generated declaration count, a local
subtask's `done`, or only one passing feedback dimension. The independent
Reviewer must reconcile the requested coverage, both proof tracks, and issues.

Require issues for demonstrated Verus limitations (including unsupported Rust
syntax) and independently reviewed std implementation/docstring defects.
Proof failure alone is not such a diagnosis. Keep unconfirmed findings marked
as needing review and distinguish a local issue draft from an externally filed
issue; resolve missing filing permission without claiming publication occurred.

Preserve the distinction between completing a planning/view subtask and
certifying an API. A tool limitation does not authorize verifier or lifecycle
engine changes: request a separate, explicitly approved tool task when needed.
Keep versions, paths, scope decisions, and current progress in the project.
