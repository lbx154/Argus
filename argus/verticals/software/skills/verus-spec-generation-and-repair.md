---
name: "Verus Module Specification Workflow"
description: "Specify Rust submodules with view-first contracts, Verus correctness and spec-determin-tool completeness feedback, reviewed implementation-change issues, and explicit limitation reports."
---

# Verus Module Specification Workflow

For Verus module-specification tasks, all four roles must use this shared
contract and their own role Skill. This is a domain delivery requirement, not
a new scheduler stage or permission to modify Verus. Keep project paths,
versions, target counts, commands, and progress in the project.

| Role | Responsibility and own Skill |
|---|---|
| Manager | [Scope, baseline, and delivery requirements](manager/formal-verification/verus-spec-generation-and-repair.md) |
| Planner | [Submodule analysis and dependency ordering](planner/formal-verification/verus-spec-generation-and-repair.md) |
| Engineer | [Views, API specs, both proof tracks, and repair](engineer/formal-verification/verus-spec-generation-and-repair.md) |
| Reviewer | [Independent correctness and completeness acceptance](reviewer/formal-verification/verus-spec-generation-and-repair.md) |

Front-door/SELF scoping uses the Manager requirements. Cross-role consultation
does not transfer authority or replace another role's work.

## 1. Analyze the module before generating API specifications

Start by inspecting the module's actual submodules, types, impl blocks,
re-exports, and API dependencies. Assign every in-scope API a canonical identity
and a submodule owner. Distinguish existing-vstd coverage, APIs needing new
specifications, and explicitly justified exclusions; do not silently drop hard
targets. This decomposes the work, not the canonical Rust source files.

For each submodule, decide whether existing vstd views suffice. If a new view
is needed, define it before writing dependent API contracts, and identify its
source/representation relation and proof obligations. Shared view definitions
and prerequisite lemmas precede dependent submodules. Constructor-dependent
correspondence may be proved together with the constructor, not used as a
circular prerequisite; the source bridge must be checked before API acceptance.
A new observer with no justified state connection is not a completed view.

Then specify APIs bottom-up according to real dependencies. Another order is
allowed when explained by the implementation or proof dependencies; it must
still define needed views before dependent contracts. Keep mutually dependent
APIs together and explicitly schedule their shared obligations. Prefer
constructors before observers and consumers when the latter require constructor
relations.

## 2. Use both source code and docstrings

Read the implementation and docstring for every API during generation and
repair, including delegated calls, generic bounds, safety requirements, panic
conditions, and relevant examples. Record both source and docstring locations.
Resolve material disagreements instead of silently choosing whichever makes
verification easier.

Preserve public bindings, signatures, lifetimes, and the intended input domain.
Do not add `Copy`, `Eq`, or `'static` merely to make proofs pass. Preserve
existing vstd contracts unless changing them is explicitly in scope.
Use direct `@`, `Seq`, indexing, subranges, and existing abstractions where
adequate. A helper should express reusable structure, not hide the API's answer.

Select the observations explicitly: returned contents, variants, order, changed
regions, and required writeback. Pointer metadata alone does not establish
pointee contents. Signature-only, domain-only, and trivial length-only contracts
are partial results, not complete content specifications.

## 3. Require two independent feedback dimensions for each API

Both dimensions must refer to the same candidate specification, views, input
domain, and intended Rust/Verus baseline.

| Dimension | Required proof | Not a substitute |
|---|---|---|
| Correctness | A source implementation proof, verified by Verus, that the API implementation satisfies the candidate contract | Typecheck, source review, native tests, or a client that assumes the candidate contract |
| Completeness | A replayable proof/check generated and successfully checked through `spec-determin-tool` for the candidate's selected observations | An ad hoc replacement checker, hand-written success summary, or an unrelated client proof |

For correctness, verify the source body or a faithful source-derived body with
its source-correspondence obligations discharged. Name the real API, source
spans, and existing trusted dependencies. An independent model without that
bridge is not an implementation proof. Do not assume the target postcondition,
replace the implementation with a synthetic `loop { }`, or hide it behind
`external_body`, `admit`, a new axiom, or a trusted wrapper. A run that checks no
relevant proof obligations does not count.

For completeness, resolve the project's actual `spec-determin-tool` entry point
and use its supported command and generated harness; do not invent a CLI or
silently substitute another tool. Retain the comparison, query/proof inputs,
result, and replay command. A successful result is relative to the stated view
and admissible boundary. If source/docstrings permit multiple answers, justify
the output equivalence independently; do not erase required observations or
place the answer in the premises.

Determinism is not correctness. Contradictory contracts and false preconditions
can also make a query vacuous. Check relevant lawful witnesses and input-domain
fidelity. Do not count `UNKNOWN`, skipped obligations, compile failure, or an
empty proof run as success on either axis.

## 4. Repair until both dimensions pass

```text
source + docstrings -> view -> API candidate
                                  |
                      +-----------+-----------+
                      |                       |
              Verus correctness     spec-determin-tool completeness
                      |                       |
                      +-----------+-----------+
                                  |
                 either unsuccessful -> diagnose -> repair -> rerun
                                  |
                 both pass for the same candidate -> independent review
```

Fix the diagnosed contract, view, source-proof translation, or checking
encoding without changing the API's intended semantics. Repair may remove an
overstrong clause or add a missing relation; it is not always strengthening.
A change to a shared view/helper invalidates dependent proof results as well.
Rerun both affected tracks after semantic changes; reuse only results whose
candidate, dependencies, and baseline remain unchanged.

Use precise failures or counterexamples to guide the next attempt. Do not
repeatedly run an unchanged broad suite, weaken a required check, or narrow
legal inputs just to get a pass. If an actual source/tool limitation prevents
closure, retain the candidate and proof attempts, and raise the corresponding
issue below. That API remains incomplete; reporting a blocker or raising an
issue is not a successful repair.

## 5. Raise issues before implementation changes

Implementation changes require a raised issue and strict independent review
before adoption. This includes canonical Rust/std bodies, executable rewrites
or desugaring in source-derived proof copies, and verifier/tool implementation
changes. Do not disguise executable changes as proof cleanup. Ordinary proof
annotations and lemmas that leave the executable implementation unchanged do
not by themselves require an implementation-change issue.

Raise an issue before making an implementation change. Include the proposed
diff and its rationale; any exploratory patch must remain an isolated,
unaccepted proposal until reviewed. Reviewer must compare original and proposed
code, source/docstring semantics, safety and behavioral effects, source-to-proof
correspondence, and affected correctness/completeness proofs. Strict review is
not replaced by a green build or self-review. Changes to the canonical source,
Verus, or the trusted basis also need explicit operator authorization; a review
approval alone does not grant that permission.

The following findings must also be raised as issues:

| Category | Required basis |
|---|---|
| `verus-limitation` | A reproducible limitation on the intended baseline, such as unsupported Rust syntax, with a minimal source example, command, and actual diagnostic |
| `stdlib-implementation` | Independent review confirms an implementation defect after investigating the failed proof and ruling out a faulty spec, view, proof, or checker encoding |
| `stdlib-docstring` | Independent review confirms an incorrect docstring or a source/docstring discrepancy, with the exact passages and expected versus actual behavior |

Proof failure or `UNKNOWN` alone is not evidence that std is wrong or that a
Rust feature is unsupported. Keep unconfirmed diagnoses as `needs-review`, not
confirmed upstream defects. A workaround that rewrites executable source for
Verus compatibility belongs in the same limitation/proposal issue and requires
the same strict review. Group repeated failures by root cause and link affected
APIs rather than opening an issue for every retry.

Each `issues/<issue-id>.md` should contain the category/status, affected
submodules/APIs, source and docstring locations, intended tool/source baseline,
minimal reproduction and actual output, expected versus observed behavior,
proposed change/workaround when any, independent review outcome, authorization
state, and links to the affected proof results. Preserve the original finding;
proofs against a changed implementation must identify that new baseline.

The local `issues/` record is mandatory. File in the configured external issue
tracker when authorized, and retain the real issue URL and filing status.
If the target or authorization is missing, ask Manager and mark
`awaiting-filing`; never describe a local draft as an already-filed upstream
issue. Do not silently patch the implementation or docstring to make a proof
pass. Keep unresolved affected APIs incomplete while independent work proceeds.

## 6. Required deliverables

At minimum, deliver all four collections:

| Collection | Contents |
|---|---|
| `specs/` | Organize the whole module by default under `specs/<submodule>/`, with one or several spec/view files per submodule, shared views where needed, and a buildable module aggregate |
| `proofs/correctness/<submodule>/<api>.rs` | Per-API source implementation proofs checked by Verus |
| `proofs/completeness/<submodule>/<api>.rs` | Per-API completeness proof/harness artifacts produced for `spec-determin-tool` |
| `issues/` | `issues/INDEX.md` plus the discovered-problem and implementation-change proposal reports, review decisions, and external filing references when applicable |

Mirror the submodule map in the specs rather than mixing unrelated APIs into
one flat file. Put genuinely shared views in shared spec files instead of
duplicating them. A root aggregate can include the submodule specs.
Repository-native equivalent layouts are acceptable when explicitly mapped to
these four deliverables.
Keep any checker-native query files and replay logs beside the relevant proof.
A status JSON alone is not the required proof artifact.

Keep a small per-API index linking canonical API, source/docstrings, view/spec,
correctness proof and command/result, completeness proof and command/result,
and any associated issue IDs. An existing manifest or concise file headers may
serve; do not invent a large new evidence protocol. Shared proof helpers are
allowed, but every API needs an identifiable replayable proof entry for each
dimension. Preserve attempted proofs and failure status for blocked APIs instead
of omitting their rows. If no issues were found, `issues/INDEX.md` says so and
states the reviewed scope; do not invent issues to populate the directory.

Accept an API only after both dimensions pass for its current candidate.
Accept the whole module only after coverage and all four artifact collections
are reconciled. A planning or view-only task can complete its own bounded work
without claiming that dependent APIs or the module have passed.

## 7. Keep verification and borrowing boundaries intact

Keep the intended upstream Verus/vstd baseline fixed. Ordinary proof lemmas
and requested `assume_specification` declarations are specification work;
lowering, borrow checking, lifecycle tracking, trusted intrinsics, and axiom
changes are a separate tool task requiring a minimal reproduction and explicit
approval. An isolated verifier fork still changes the trusted basis.

Use existing borrowing support for value writeback. For a whole borrowed slice,
the familiar relation is `slice@ == old(vec)@` together with
`final(slice)@ == final(vec)@`. For two regions, reconstruct with
`final(slice)@ == final(ret.0)@ + final(ret.1)@`, specify initial subranges,
and preserve any unborrowed complement. `final` concerns the relevant borrow's
final value, not proof that `Drop` ran. Allow lawful caller writes.

Do not add owner IDs, allocation tokens, or lifecycle event state machines by
default. Destructor-dependent effects need explicit support when required;
`forget` can skip cleanup. Distinguish borrowed `&mut T` from moved-out `T`.
An observer's equality with `remaining` at construction does not establish
the equality after consumption. Check constructor/observer/consumer composition.

Thread callback, `FnMut`, `Clone`, and relevant `Drop` states faithfully.
Do not fill semantic gaps with answer-selecting UFs or move opacity into a
backend. Existing vstd abstractions remain usable only for justified relations.
Native tests, mutation controls, and client proofs are useful supplements, not
replacements for the two required proof tracks. Never execute native UB negatives.

Relevant vstd examples include `ref_mut_array_unsizing_coercion` in
`source/vstd/array.rs`, `Vec::as_mut_slice` and `vec_index_mut` in
`source/vstd/std_specs/vec.rs`, and `first_mut`, `split_at_mut`, and `iter_mut`
in `source/vstd/std_specs/slice.rs`. Inspect the selected version rather than
assuming all versions have identical definitions.
