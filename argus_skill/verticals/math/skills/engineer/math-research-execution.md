---
name: "Math Research Execution"
description: "Execute mathematical scope and solve work with honest result classification, statement fidelity, and optional real Lean compilation."
---

Do the mathematics in the form that best fits the problem. Distinguish a proof,
counterexample, construction, finite experiment, formal verification, known
result, and conjecture; do not describe one as another.

When the role banner reports a Jacobian capability, use the Argus adapter rather
than importing Jacobian into the Argus environment. Discover a small set of
candidate operations, inspect the exact typed contract, then execute one bounded
operation. Preserve the operation id, exact payload, typed output, and adapter
error/timeout status in the project evidence. Jacobian supplies mathematical
moves, not a proof strategy: do not force a nearby operation onto the claim, and
do not treat timeout, incomplete output, or UNKNOWN as support or refutation.

Start from the exact question and the requested bar. If a complete proof is
required, useful failures and computations do not make the mission complete.
Keep a short note about a failed route in the existing `CHECKPOINT.md` when it
will help the next attempt, then change the mathematical approach.

In `scope`, settle what is already known before the mathematics starts: the
results this work will lean on go into the ledger as assumptions with their
citations, and so does an approach already known to fail. This is not a survey
and completeness is not the bar — it is where the retrieval gets paid. Workers
on the same goal cannot see each other's searches, so a lookup done here costs
once and the same lookup done in `solve` costs once per worker. Finding nothing
relevant is a result; so is being unable to obtain a source. Record either.

Use ordinary working files suited to the task. Do not create process-only
planning, audit, status, or evidence-packet files merely to satisfy the
workflow. The theorem, proof, counterexample, code, or formal source is the
evidence, and no fixed bundle of output filenames is required.

One file is an exception once a `targeted` project has settled on a route and
reached `develop` or `certify`: `research/PROOF_GRAPH.json` records what still
stands between the current state and the goal. It is not process paperwork —
without it "how hard was this step" silently replaces "how much closer did this
get us". Under `explore` it is not required.

A route that died is recorded the same way every other fact about the work is,
in `research/MATH_STATE.json`: `retire-route --id <route> --retired-because
"<what killed it>"`. Retiring without a reason is the failure mode worth
naming — the next person re-opens exactly the route you just closed, because
"retired" alone does not tell them whether the idea was wrong or merely
unfinished. Retired routes and their reasons are projected back into the
context you are given; there is no separate ledger file to maintain.

When continuing earlier work, compare the new result with the strongest prior
result that matters. Explain the mathematical improvement directly; no special
tracking file is required.

Lean is optional; a committed `.lean` file is not. Once one exists, completing a
stage requires it to show a fresh real compiler run with no proof holes, so run

    python -m argus_skill.verticals.math.lean_evidence verify Main.lean \
        --statement-fidelity statement_fidelity.md --claim C1

which compiles the source, records the answer beside it stamped with the source
hash, and — because of `--claim` — writes the outcome into the claim ledger as
mechanical evidence. Editing the source after a run invalidates that record;
re-run it. Editing it *during* one is different: the run is refused outright and
nothing is written at all, because the compiler's answer would then be about text
the project no longer carries. Neither `Main.lean` nor `statement_fidelity.md` is
yours to touch while `verify` is running — do other work, or wait. If the host
has Mathlib installed it is used automatically, so `import Mathlib` needs no
extra flag. `--claim` is the only way mechanical evidence is ever
written: there is no flag that lets you record a compiler verdict you did not
get, and asking for one is a bug report rather than a request. Formalizing
several claims in one directory is fine and needs no filename scheme of your
own: each run archives its own certificate under `research/lean/certificates/`
and the claim cites that, so reusing the names above does not cost the previous
claim its evidence.

When the compile is long enough that waiting it out is the expensive part — a
Mathlib import is minutes — `submit` takes exactly the arguments `verify` takes,
prints a handle, and returns; `reclaim <handle>` later writes the same records on
the same terms, or says the compile is still running and writes nothing. It
compiles a copy taken at submit, so unlike `verify` you may keep editing while it
runs — but the answer is only publishable if `Main.lean` and
`statement_fidelity.md` still match what was compiled, so an edit you keep means
submitting again. A run whose process was killed is reported as lost, never as a
failing proof. `status` lists what this host still owes you. Use `verify` for a
compile you would have waited out anyway; a handle you forget to reclaim is a
compile nobody paid for.

Compilation checks the theorem you encoded, not the one you meant, so the
separate `statement_fidelity.md` states which objects, quantifiers, hypotheses,
and conclusion the formal statement carries and names the declarations it
describes. A compiling proof of a mistranslated statement is the most expensive
wrong answer available here. The document is hashed into the compiler result, so
rewriting it afterwards invalidates the run rather than quietly re-labelling it,
and rewriting it while the compiler is running refuses the run the same way the
source does.
Nothing checks that the document is *true* — that half of the argument is yours,
and it is why a proved claim still reports what nobody verified.

Write that document once and write it right; a reading that was correct does not
need rewriting. If you do rewrite it and re-verify, the compile is unaffected but
the reading it is paired with is a different one, so the certificate the claim
stood on is retired in favour of a new one and anyone who judged the old reading
is asked again — `check` reports each such verdict until they do. That is the
cost of changing what the theorem is taken to say, and it is the right cost:
statement fidelity is the one question the compiler does not answer, so it is the
one approval that must never be inherited by a document nobody read.

If the toolchain or a library such as Mathlib is missing, the run is recorded as
unverified and still blocks: that is an environment fact rather than a
mathematical verdict, but an unverified formalization is not evidence. Argue in
prose instead of committing a `.lean` file you cannot check.

## Recording what the project believes

`research/MATH_STATE.json` is the ledger of claims and what supports each one.
Status is derived, never written: `closed_kernel` is what a compiler earned, and
there is no argument you can type that produces it. Keep it current with

    S="python -m argus_skill.verticals.math.math_state"

    # the problem statement every claim is stated against, once per project
    $S context --id ctx --statement "..." --define "term=meaning"

    # one mathematical assertion; --formal-file points at the Lean source
    $S claim --id C1 --context ctx --statement "..." --formal-file research/lean/Main.lean

    # a result taken from elsewhere: holds C1 at conditional_kernel until retired
    $S assume --claim C1 --id RH --by "engineer:you" --statement "..." \
        --source "Riemann 1859" \
        --source-id "doi:10.1093/oso/9780198533696.001.0001" --locator "Theorem 14.2"

    # one decomposition of a goal into obligations; records a plan, confers nothing
    $S route --id R1 --goal C1 --obligation L1 --obligation L2

    # when that route dies: the obstruction, in your words, written once
    $S retire-route --id R1 --because "the bound is unavoidable below dimension 3"

    # your own or a reviewer's opinion, recorded as an opinion
    $S judge --claim C1 --verdict supports --by "reviewer:alice" \
        --artifact research/lean/certificates/C1-<digest>.json

    # the next version, when the definitions or the theorem change
    $S revise-context --id ctx --define "term=corrected meaning"
    $S revise-claim --id C1 --formal-file research/lean/Main.lean
    $S revise-claim --id C1 --retire "RH=Lemma 2 gives the bound unconditionally"

    # after revising a context: every claim stated against it, one at a time
    $S revise-claim --id C1 --use-current-context

    # what it all adds up to, and structural defects
    $S show --claim C1
    $S check --project-root .

Record a context and a claim before formalizing, so `verify --claim` has
something to attach to. Record an assumption the moment the proof starts leaning
on an unproved result — an undischarged assumption is the difference between
`conditional_kernel` and `closed_kernel`, and it is invisible unless written
down. Record a route when a goal splits into steps, so a retired decomposition
is not retried. Record a judgement when you or a reviewer have read a proof that
no checker can check.

A citation names a proposition, not a paper. `--source-id` gives the document
canonically and with its version (`arxiv:2504.01234v2`, `doi:...`, `isbn:...`),
because theorem numbering moves between revisions and "Theorem 3.2" of the wrong
version is a different result; `--locator` names the proposition inside it. Give
both or neither — half a citation cannot be looked up, and `check` says so. With
neither, the prose `--source` stands and `show` reports the citation as
`uncited` rather than `unchecked`: a private communication or an unpublished
note has no locator, and that is a legitimate answer, not an omission. What is
not legitimate is a DOI you did not read; correcting a locator later mints a
different assumption, and any check obtained against the old one stops counting.

Revising a context supersedes it for every claim stated against it, and `check`
reports each one as `claim_context_outdated` until you say what happened to it.
That is the point: a corrected definition can turn a proved theorem into a
statement about something else, so the claims do not follow the context along
silently. Re-state each one with `revise-claim --id ID --use-current-context`
once you have read it against the new definitions and it still says what you
mean — and if it no longer does, restate the theorem instead.

Two things this ledger deliberately will not let you do. Restating a claim mints
a new version and the evidence bound to the previous statement stops counting —
that is the cost of retranslating, not a bug to work around. And you cannot stop
standing on an assumption without writing why: `revise-claim --retire ID=reason`
takes the reason because deleting a dependency asserts the proof does not need
it, which is itself a mathematical claim.

## Opening several routes at once

A goal with two plausible attacks has two routes, and the ledger already says
what that means: the obligations inside a route are an AND, several routes for
one goal are an OR, and neither confers anything on the goal. Record them before
anyone starts — `$S route --id R1 --goal C1 --obligation L1` — because an
unrecorded alternative is one the next worker re-derives from scratch, and a
route that dies without `$S retire-route` is one that gets retried.

Then dispatch them. `argus_builtin_skills/agent-team-lead.md` is the
mechanism: one task per route, the pool width set to how many you actually want
running. How many that is, is your judgement. The test is not how much compute
is free — it is whether the routes fail for different reasons. Two routes that
die to the same obstruction were one route dispatched twice, and you wait for
both.

Whoever picks up a route is the one thinking about it. Give them the goal, the
route's obligations, and what is already known not to work; do not hand over a
decomposition into steps, because that decomposition is the mathematics you were
asking somebody else to do. Put the goal claim's id in the task's
`acceptance_check` and name that one claim there and no other — that field is
what hands the worker everything already recorded about the claim, including
which of its citations somebody has been to the source for, and a field naming
two ids resolves to none. A route objective naturally says "reduce C1 to L2",
which names two, so the terser field is the one that has to carry it. What comes
back is a result or a reason the route is dead, and the reason goes into
`$S retire-route --id R1 --because "..."` in your words, since you are the one
holding the OR and the next planner reads it there.
It is written once: a route you come to believe in again is a new plan with a
new id, not a rewritten reason on the old one.

The team gate asks for non-overlapping writable paths, and here they overlap in
exactly one place: `research/MATH_STATE.json`. That one is safe to share. Every
write takes an exclusive lock before it reads, and each worker records its own
claims, assumptions, and evidence, so concurrent recording is what the ledger
was built for. What must not be shared is a working file — a proof draft, a Lean
source, a `statement_fidelity.md` — where two workers overwrite each other with
no lock and no merge. Give each route its own directory *inside* the project
tree, name it in the task's `owns_paths`, and leave the task's `cwd` at the
project root. `$S` writes to `research/MATH_STATE.json` under whatever directory
it runs in, so a route dispatched into its own `cwd` quietly gets a private
ledger nobody reads — every claim it records, every citation it checks, and the
OR you are holding all stop meeting anywhere.

## Checking what you cited

Every cited proposition has to be looked up before anything is delivered, and
nothing about that waits on you mid-proof. Run

    C="python -m argus_skill.verticals.math.citation_check"

    $C status                                     # what still owes a lookup
    $C resolve --claim C1 --assumption RH         # does the document exist
    $C attribute --claim C1 --assumption RH \
        --excerpt-file read.txt --verdict supports --by "reader:whoever-looked"

whenever it suits — between routes, while a compile runs, or as a task handed to
another worker. The work list is derived from the ledger rather than stored, so
several people can check at once without coordinating and a repeat costs
nothing. `scope` and `solve` complete with citations outstanding. `review` does
not: it is the delivery point, and a proof leaning on a theorem nobody opened
the source for is a proof with a hole in it that no compiler will ever find.

`resolve` asks the registry whether the identifier exists. It is the cheap catch
for the fabricated reference and it settles nothing by itself — a DOI that
resolves proves a paper is there, and your citation was about a theorem inside
it, so a successful lookup is recorded as `inconclusive` and the citation stays
open. Only `attribute` closes one, because only a reader can answer the question
that was asked.

So `--excerpt-file` holds the passage you actually read at that locator, and it
is archived under `research/literature/` before your verdict is recorded against
it. That is the whole reason this is `literature` evidence rather than your
opinion: a later reader can open what you read and disagree. Quote the statement
including its hypotheses — a paper that has the theorem under conditions that do
not hold here is the failure this is for, and it is invisible in a summary. A
`refutes` needs an excerpt too: quote what is actually at that number.

One citation you cannot close is your own. `attribute` refuses a `supports`
verdict filed under the same name that ran `assume`, and a citation whose only
support came from its filer reports as `self_checked` and does not clear
delivery. This is not a comment on your care. You wrote "Theorem 3.2 of [K]"
because you believed [K] has a Theorem 3.2 saying that; your going back and
agreeing is that belief a second time, and the reading is exactly what is in
question. Hand it to the Reviewer or to another worker. The other direction is
always open: if you go back and find it is *not* there, file the `refutes`
yourself — that is the one answer self-checking cannot manufacture, and it is
worth more than any confirmation.

If the source cannot be obtained at all, say so instead of leaving the lookup
open — restate the assumption with `--source` alone and no `--source-id`, which
reports it as `uncited` and puts the situation in front of the reviewer rather
than behind a queue nobody can clear.

Before investing heavily in a new conjecture, a small counterexample search may
be useful. For a construction, check that the object satisfies every condition.
Use literature only when a known result matters or when claiming novelty. These
are mathematical choices, not boxes that must all be checked.
