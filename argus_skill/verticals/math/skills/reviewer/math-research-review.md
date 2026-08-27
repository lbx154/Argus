---
name: "Math Research Review"
description: "Independently review mathematical correctness, novelty, significance, statement fidelity, and real Lean evidence against the requested research target."
---

Review the mathematics, not the paperwork. Missing scope documents, ledgers,
graphs, audit reports, or evidence bundles are not defects by themselves, and
their presence is not evidence of correctness.

Read the original question and the actual result. Check the important steps,
assumptions, quantifiers, dependencies, and conclusion. A finite computation is
not a proof of a universal claim. A counterexample or construction must satisfy
the original conditions.

Early on, one thing is worth insisting on: that the known status of the problem
was established here, and written down, rather than left for later rounds to
rediscover. Every worker who has to find out for themselves what is already
proved spends the same hours to reach the same place, and the ones who skip it
re-prove a published theorem.

If a complete proof was requested, return `done` only for a complete proof; an
honest failed attempt remains useful but incomplete. If the task asks for
continued strengthening, compare the new result directly with the strongest
prior one. A bounded subproblem can be done without claiming that the whole
research goal is complete.

## Say which layer failed

When the round is incomplete, name where it failed. The verdict says whether
to continue; this says what to change, and without it every failure gets
patched locally.

- `proof` — the plan holds and this argument has a gap. Fix the argument.
- `plan` — the subgoal decomposition or its dependencies are wrong. Re-derive
  the subgoals; the approach may still be sound.
- `strategy` — the approach itself is not worth continuing. Say so plainly and
  record the evidence that retires it, so the route is not proposed again
  under a new name.

A `targeted` project that has drifted into proving some method can never work
is a `strategy` failure, however rigorous that work is. Ruling out a
sufficient criterion does not settle the original question.

## Local progress is not gap reduction

Distinguish a result that shrinks the distance to the goal from one that is
merely new. Extending a finite verification to a wider range, more moduli, or
more primes produces a fresh artifact and no gap reduction; a finite
computation is not a proof of a universal claim, and repeating it at a larger
bound does not become one. Do not accept a round whose only increment is the
same verification at a larger bound — say which proposition moved, or that
none did.

When Lean is used, inspect the source and a fresh real compiler run, and check
that the encoded theorem means what the original problem says. Do not require
particular filenames.

Where `research/MATH_STATE.json` exists, `python -m
argus_skill.verticals.math.math_state show` reports each claim's derived status
and, on any claim a compiler established, the caveat that nothing checked
whether the formal statement says what the natural statement says. That is your
job, not the compiler's, and it is the review a `closed_kernel` most needs.
Read it from the claim's own `certificates` entry rather than from whatever is
in `research/lean/` now: `verify` republishes fixed names, so a directory where
two claims were formalized holds the last one's source and compiler result,
while each certificate keeps the source text and fidelity note that were
actually paired with that claim's run. A claim with no `certificates` entry has
no evidence bound to the statement it currently carries — if it once did, the
statement was restated afterwards and the old certificate no longer describes
it.
Record the outcome with `math_state judge --claim ID --verdict ... --by you
--artifact <the certificate you read>`, including the `inconclusive` verdicts —
a step you could not settle is a result. Judgement promotes nothing; a claim's
status will not move because you agreed with it.

Name the certificate. A fidelity verdict is about a particular reading of the
theorem, not about the claim forever: if the fidelity note is later rewritten
and the proof re-verified, the compiler answers as before but the reading it is
paired with is a different one, and a verdict that named the old document is
reported by `check` until whoever gave it reads the new one and judges again. A
verdict that cites nothing is not reported — not because it is safer, but
because it never said which document it was reached from, so it silently carries
over to a reading nobody reviewed. That is the one approval that must not be
inherited, so cite what you read.

## The sources the proof leans on

`python -m argus_skill.verticals.math.citation_check status` lists every result
the project imported and whether anyone has been to look it up. Nothing is
delivered while one is outstanding, and you are the right party to close them:
the worker who wrote "Theorem 3.2 of [K]" is the one whose reading is in
question, so their own confirmation of it is the assertion under review, not a
check of it. That is now the program's rule and not only yours — `attribute`
refuses a `supports` verdict filed under the name that recorded the assumption,
and a citation supported by nobody else reports as `self_checked` and blocks
delivery until someone independent goes and looks. Which usually means you.

    citation_check attribute --claim C1 --assumption RH \
        --excerpt-file read.txt --verdict supports --by "reviewer:you"

The excerpt is what makes this the one tier besides judgement you can write. It
is the passage you actually read at that locator, archived before your verdict
is recorded against it, so the next reader can open it and disagree — which is
the whole difference between a literature check and an opinion. Quote the
statement with its hypotheses. A source that has the theorem under conditions
that do not hold in this setting is the failure worth catching here, and it
survives every summary of itself.

Check the proposition, not the paper. A resolving DOI settles nothing, which is
why a successful lookup is recorded as `inconclusive` and leaves the citation
open. And a confirmed citation discharges no assumption: the source really says
it, the claim still stands on it, and whether its hypotheses apply here is a
third question that belongs in your proof review.

Say plainly whether the outcome is proved, disproved, computational evidence,
partial progress, a conjecture, or unresolved. Check relevant primary sources
only when novelty is claimed or required by the requested ambition; otherwise
leave novelty unknown rather than demanding a separate audit artifact.

Fill any structured result field required by the active schema, but do not
duplicate the same judgment in extra reports.
For Jacobian-backed evidence, independently compare the inspected operation's
postcondition with the exact claim. When this review backend permits command
execution, rerun the recorded operation id and payload and inspect the typed
result; otherwise require a Host-recorded bridge run and review its exact
payload/output rather than pretending a rerun occurred. A successful process
exit proves only that the operation returned; the returned mathematical value
must itself witness the claimed refutation or check. Timeout, transport failure,
incomplete output, and UNKNOWN remain non-conclusions.
