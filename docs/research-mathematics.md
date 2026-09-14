# Mathematical Research in Argus

This document describes the `math` vertical: what it is for, how a problem
moves through it, and what the system will and will not accept as evidence
that something has been proved.

The design rests on one distinction. A mathematical claim is not settled by
how confident anyone is about it, and it is not settled by how much work went
into it — it is settled by a check that a machine, or a reader with the source
in hand, can repeat. Everything below follows from taking that seriously:
evidence is bound to the exact text it was produced against, the record of
what happened is append-only, and the status of a claim is computed from that
record every time it is asked for rather than stored anywhere.

---

## 1. Entering the vertical

A math project begins the way any Argus project does: the operator states a
problem at the Manager's front door. The Manager selects the vertical and
writes a goal contract, which fixes the problem statement, any precise
constraints the operator gave, anything they ruled out, and any question the
request leaves genuinely open. Constraints are transcribed, never invented — a
requirement nobody asked for becomes a goal nobody agreed to, and where a
number is clearly needed but was not supplied, that is recorded as an
ambiguity rather than guessed.

Alongside the contract the project acquires an **objective mode**, one of
`targeted` or `exploratory`:

| Mode | Meaning |
| --- | --- |
| `targeted` | A specific proposition is to be proved or refuted. |
| `exploratory` | The territory is to be surveyed; no single statement is the target. |

The mode is the operator's to choose, and the runtime never chooses it for
them. What it does instead is *transcribe*: when the operator's opening
request already states what they want proved, that statement becomes the goal
and the source is recorded as `transcribed_from_request`, so an operator-set
value and a transcribed one are distinguishable on disk. Transcription is
always to `targeted` — the stronger of the two bars — so nothing is ever
certified against a bar looser than the operator would have picked. An
operator-supplied value is never overwritten.

The mode matters because an unset mode blocks every math stage by design,
including `scope`. Transcription is what makes a math project created through
the ordinary product front door able to close a stage at all.

---

## 2. The stage contract

Math runs three stages:

```
scope  ──▶  solve  ──▶  review
```

The workflow mode is `proportional` (as opposed to `staged` or `direct`):
evidence reuse within a stage is proportional to what changed. Once a Reviewer
certifies an artifact, later missions verify the new claim or delta rather than
rebuilding the whole provenance tree — unless a concrete conflict reopens it.

Each stage maps to a **verification profile**, which is the system-wide
setting for how strict a check has to be before it counts:

| Stage | Profile | What the stage is for |
| --- | --- | --- |
| `scope` | `explore` | Settle what the problem is and what is already known about it. |
| `solve` | `develop` | Do the mathematics; maintain the proof-gap structure. |
| `review` | `certify` | Deliver; block until every cited proposition has been read. |

Three coarse stages map cleanly onto the three profiles with nothing left
over. `review` is the only stage that blocks on full citation coverage, which
is this vertical's form of the completeness requirement other verticals
express differently.

Two further properties of the contract:

- **`REQUIRE_INDEPENDENT_REVIEW = True`.** A mission cannot settle on the
  Engineer's own account. A separate Reviewer sees the work.
- **`completion_gate = "none"`.** Unlike the `research` vertical, math does
  not gate project completion behind a certification artifact. Mathematics is
  finished when the mathematics is finished, and the system does not pretend
  to be the judge of that; the gate that does exist is on the evidence, not on
  a submission ceremony.

Live literature search is available to the Engineer during `scope` and
`solve`, and withdrawn during `review` — by then the sources should have been
found and read, not discovered.

One checklist item is protected: `review.goal-achieved`. Protected items may
not be removed or weakened by a project editing its own checklist. A project
may not tidy away the question of whether it did the thing it set out to do.
Because math's completion gate is `none`, this is the only protected item it
carries — the shared submission-assurance set applies to `certified` verticals
only.

---

## 3. The evidence kernel

The `argus/proof_ledger` package is the core. It knows nothing about
Lean, about literature, or about the stage machine; it knows about claims,
evidence, and what follows from them. It also imports nothing from Argus and
nothing outside the standard library, which is enforced by an AST sweep in
`tests/proof_ledger/test_proof_ledger_kernel.py` — it is meant to lift out into
its own repository without edits.

Host-neutral is not domain-neutral, and it is worth being precise about which
half is which. The record layer below — the digest binding, the append-only
store, the derived-never-stored status — has nothing mathematical in it. The
decision layer does: the kernel and discharging tiers are both `{MECHANICAL}`,
and a claim is granted kernel status only if it carries a formal statement. A
domain whose claims cannot be formalised can import this package and will then
never close a route. What the package is built to be reused for is another
domain that discharges claims against a mechanical checker.

### 3.1 What is recorded

| Record | What it is |
| --- | --- |
| `ClaimVersion` | A statement, at a specific version. Revising a claim mints a new version rather than editing the old one. |
| `EvidenceRecord` | One check that was performed, its tier, and its verdict. |
| `ExternalAssumption` | Something the argument depends on but has not proved — typically a cited result. |
| `ProofRoute` | A plan: a set of obligations which, if all discharged, would establish a claim. |

**Evidence binds to content, never to a version number and never to a
timestamp.** An evidence record names the digest of the exact statement text
it was produced against. Edit the statement and the old evidence no longer
binds — not because a rule fired, but because the thing it was about is no
longer there. This is the single property that prevents the most damaging
failure mode in a system like this: a proof that was checked, a statement that
was then quietly reworded, and a certificate that still reads green.

The store is one append-only JSON file at `research/MATH_STATE.json`.
`add_claim` refuses to overwrite an existing `(claim_id, version)` pair;
`revise_claim` mints the next version. Locking lives in the adapter, not the
store.

### 3.2 Status is derived, never stored

No claim carries a status field. Status is computed from the evidence records
each time it is asked for, which means it cannot drift out of agreement with
the evidence and there is no state to migrate when the rules change.

`ClaimStatus` has five values, and the distinction the design refuses to
collapse is the last two:

| Status | Meaning |
| --- | --- |
| `proposed` | Asserted; nothing has checked it. |
| `supported` | Some non-kernel channel says yes. Most of a live project sits here. This is not a proof. |
| `refuted` | A counterexample or a kernel says no. Outranks every support. |
| `conditional_kernel` | A kernel verdict binds to this exact statement, with at least one external assumption still open. Correct *modulo* results not yet checked. |
| `closed_kernel` | A kernel verdict with every external assumption discharged. |

`refuted` outranking support is deliberate: an argument and a counterexample
cannot both stand, and a system that prefers the argument is a system that
keeps working on a dead claim.

`RouteStatus` is similarly careful. A route that is `discharged` has proved
every obligation and has still proved *nothing* about the goal, because
nothing has checked that the obligations imply it. A `blocked` route — one
whose obligation was refuted — is dead as a plan while the goal may be
perfectly true by some other route. Neither status touches the goal's status.
Letting a decomposition nobody verified decide a mathematical question is
exactly the error this prevents. `retired` is not a failure state; it is the
recorded reason not to retry.

`CitationStatus` keeps `unchecked`, `disputed`, and `inconclusive` apart: a
citation nobody has looked up, one somebody looked up and could not find, and
one where the checker ran and could not settle the question are three
different facts about the world.

### 3.3 Evidence tiers

Every evidence record carries a tier, and the tiers are not interchangeable.

| Tier | Produced by | Role |
| --- | --- | --- |
| `mechanical` | The Lean pipeline | The only tier that makes a claim a kernel claim, and the only tier that discharges an external assumption. |
| `literature` | The citation checker | Records what a source does or does not say. Never establishes, never discharges. |
| `judgement` | The agent | An argued opinion. Must cite what it is about. |
| `computational` | *(no producer yet)* | Reserved. May refute; nothing writes it today. |

Three sets govern what a tier is allowed to do:

- `KERNEL_TIERS = {mechanical}` — what makes a claim a kernel claim at all.
- `DISCHARGING_TIERS = {mechanical}` — what closes an external assumption.
- `REFUTING_TIERS = {mechanical, computational}` — what may declare a claim
  false. Wider than the kernel set on purpose: refuting is cheaper than
  proving, and a counterexample does not need a proof assistant.

The governing rule is:

> **A tier may only be written by a program that performed a check of that
> kind.**

This is why there is no general "record evidence at tier X" command.
`judgement` is the one tier whose checker *is* the agent, so it is the one
tier an agent-facing command writes. `mechanical` is written by the Lean path
and reports the compiler's answer, never an argument about it. `literature` is
written only by the retrieval path, which archives the source before it
records anything. `computational` has no producer, so nothing can claim it.

---

## 4. Producers of evidence

### 4.1 Lean: proof validity and statement fidelity

Lean can decide one question and cannot decide the other:

- **Proof validity** — does this argument establish this Lean proposition?
  Lean answers this, completely and mechanically.
- **Statement fidelity** — is this Lean proposition the mathematical claim we
  actually care about? Lean has no opinion. A perfectly compiling proof of the
  wrong statement is a perfectly compiling proof.

So a Lean source without a substantive statement-fidelity document is not weak
evidence — it is unfalsifiable, and the pipeline refuses it. The fidelity
document is what a human reviewer reads to confirm that the formalization says
what the informal claim says.

Compilation runs asynchronously through `submit_lean_run` / `reclaim_lean_run`.
**The compile reads a snapshot**: `submit` copies the canonical source and the
fidelity document into a per-run staging directory and compiles that copy, so
the digest recorded in the evidence names the exact text that was compiled.
The project stays editable while a compile is in flight, and a certificate can
never end up describing bytes that no longer exist.

The pipeline also runs an **environment axiom audit**, and treats it as part
of the pass rather than as a report. A compile whose status is `success` but
whose audit did not itself succeed is rejected — including the case where the
audit never ran at all, which is not a pass. A proof resting on an unaudited
axiom is not a proof, and a `sorry` is visible as a proof hole rather than
silently green.

### 4.2 Literature: two layers, and only one decisive answer

Checking a citation is two different questions, and the system separates them:

1. **Existence** — does this source exist and resolve? Mechanically checkable.
2. **Content** — does it state the proposition the argument attributes to it?
   Not mechanically checkable.

The only decisive answer the content check can return is `refutes`, backed by
an excerpt saying what the source actually says instead. A citation that
resolves successfully is recorded as `inconclusive`, not as support: reaching
a source is not the same as having verified what is in it. `refutes` is in
`REFUTING_TIERS`, which is why the negative direction is allowed to be
decisive while the positive one is not.

The record is written only after the source has been retrieved and archived
under `research/literature/`. The excerpt — not a `--tier literature` flag —
is the thing that earns the label.

### 4.3 Judgement

The agent's own assessment, recorded honestly as such. A judgement that cites
nothing is not reported: it made no claim about which statement it concerned,
so there is nothing for a later reader to check it against. Judgements are
retired when the claim they addressed is revised.

---

## 5. Structure before proof

Under the `develop` and `certify` profiles, a project must show its structure
before it is allowed to claim a result: first a **strategy graph** (AND/OR — a
proof needs all of these, or any one of those), then a **proof DAG**. The
artifact lives at `research/PROOF_GRAPH.json`.

The `explore` profile deliberately requires neither. Demanding a formal
decomposition of an idea nobody has committed to yet produces structure that
is thrown away, and the cost is paid on exactly the work most likely to be
discarded.

---

## 6. What the agent sees

Context handed to a mission is a **depth-1 projection** of the claim
neighbourhood: the claim, its immediate dependencies as they stand right now,
and no further. The bound is set by the claim, not by the project, so context
size does not grow with how long the project has been running. An agent gets
the mathematics adjacent to its task rather than a transcript of everything
that has ever happened.

---

## 7. On-disk layout

All paths are relative to the project root.

| Path | Contents |
| --- | --- |
| `research/MATH_STATE.json` | The append-only claim, evidence, assumption and route store. |
| `research/PROOF_GRAPH.json` | Strategy graph and proof DAG. |
| `.argus/PIPELINE_STATE.json` | Stage, vertical, workflow mode, and the math goal and objective mode. |
| `research/literature/` | Archived sources, retrieved before any literature record was written. |

---

## 8. Validation

The vertical has been exercised end to end on a number-theoretic problem
supplied as a plain problem statement, with no pre-seeded state — the Manager's
own vertical decision and objective adoption were exercised rather than
bypassed. The run moved through `scope`, `solve` and `review`, certified all
three stages, produced a Lean 4 development that compiled cleanly with the
axiom audit reporting no proof holes, and exited without operator
intervention. Every structured research payload produced during the run parsed
and was accepted.

---

## 9. Boundaries

Things this vertical deliberately does not do:

- **It does not decide whether the mathematics is right.** It decides whether
  a check was performed, of what kind, against which exact text.
- **It does not treat a discharged route as a proved goal.** Nothing checks
  that a decomposition's obligations imply the thing decomposed.
- **It does not let a resolved citation count as support.** Only a refutation
  from the literature layer is decisive.
- **It does not have a computational evidence producer.** The tier exists in
  the model and in `REFUTING_TIERS`; no code writes it, so no claim can rest
  on it.
