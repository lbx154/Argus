# What Argus Grew

There are two questions you can ask about a long-running agent.

The first is *what did it learn* — which routes are dead, which measurements are
impossible, which prior work already owns the idea. That is real, and Part 6 covers
it.

But the sharper question is **what can it now do that it could not do before**. New
tools. New procedures. New capabilities that persist after the campaign ends.

This document answers the second question first, because it is the one that
matters. Everything below is quoted from the runtime's own records, produced with no
human in the loop, with paths so you can go check it.

---

## Part 1: It repaired its own runtime

Argus's retired resident self-maintenance loop ran against its own source tree. It
did not merely suggest improvements: it diagnosed defects in its own code, named the
files, wrote fixes, and stated the commands that decided whether they worked. Current
self-maintenance uses the ordinary mission flow and an operator-approved deployment
boundary instead of that resident loop.

**The historical record: 16 defects diagnosed in its own runtime. All 16 carry an
executable acceptance command.**

| Outcome | Count |
| --- | --- |
| Repaired, awaiting release | 6 |
| Active | 6 |
| Queued | 2 |
| **Failed to commit** | **2** |

Those last two are in the table because they happened. A historical record that
omitted the loop's failures would misrepresent its results.

Here are three, in increasing order of what they say about the system.

### (a) A concurrency race in its own scheduler

`projects/s-e0378b3f/self-maintenance/state.json`

> "A forward-progress replan source is terminalized before its same-tick atomic
> replacement validates the expected plan. The refuted mission correctly cannot
> rerun, but its terminal status also invalidates the only compare-and-swap witness,
> so Manager `replace` always becomes `planner_error`."

Plainly: to swap a plan safely, the system compares against the old plan and swaps
only if it still matches — a compare-and-swap. But it was marking the old plan dead
*before* the swap validated. Killing the old plan destroyed the only thing the swap
could compare against, so every replacement failed.

That is an ordering bug in atomic state transition, described in the right
vocabulary, found in its own code. It listed four runtime files and three test files
to change, and this acceptance command:

```
python3 -m pytest -q tests/life/test_planner_delegation_flow.py \
  tests/life/test_backlog_dag.py tests/life/test_planner_terminal_empty_output.py
```

### (b) Its own token waste

`projects/s-845699da/self-maintenance/state.json`

> "A validated `{"wait_for":"subagent"}` request for unchanged `RUNNING_HEALTHY` work
> pauses for only one cadence. Each `cadence_elapsed` consumes another Engineer round
> and provider call, so long self-watched jobs are repeatedly polled by the LLM until
> the mission exhausts `max_rounds`."

Plainly: when Argus waits on a background job, it was waking a language model to ask
"done yet?" over and over — spending a paid round each time to learn nothing had
changed, until the mission ran out of rounds.

Finding your own waste is a specific capability. This is a system noticing that it
is burning money on a poll loop and repairing the loop.

### (c) It caught its own code violating its own core rule

This is the one worth stopping on.

The central claim of the Argus technical report is that **the component doing the
work must not be the one that declares it finished**. Argus found a place where its
own implementation broke that rule:

`projects/e620aa0a38e7/self-maintenance/state.json`

> "Operator-answer continuations can be completed by Engineer self-review even though
> they are Manager/operator-authorized follow-up work. The continuation creator
> preserves inherited tags but does not add a required-review tag, so the runtime
> passes `require_independent_review=False` unless the blocked item already happened
> to require review."

Plainly: when a human answers a question and the work resumes, that follow-up
inherited its old tags but never got marked as needing independent review. So the
Engineer could sign off on work a human had authorized — exactly the thing the
architecture exists to prevent, reintroduced by a missing tag.

A related defect, found separately:

`projects/s-54f17ee7/self-maintenance/state.json`

> "For `require_independent_review=false`, `argus_skill/engineer/round_self_review.py`
> **silently continues any successful Engineer turn** that lacks
> `MILESTONE_STATUS=done`. This bypasses existing round settlement/escalation logic,
> allowing compact Engineer-only continuations to run nearly to `max_rounds` with high
> cost before a late blocker."

Both are the same shape: a path where the executor escapes review. Both were found by
the system auditing itself against its own contract, not by a human reading the diff.

And the first one is one of the two marked `commit_failed`. It diagnosed the
violation, wrote the acceptance test, and did not land the repair. That is in this
document for the same reason it is in the record.

---

## Part 2: It turns "you cannot measure that" into a program you can run

Stating that an experiment cannot work is an opinion. Argus builds the witness.

During one campaign it concluded that a planned measurement was not merely unproven
but **impossible with the planned instrument** — no amount of testing could separate
the two competing explanations. Rather than leaving that in prose, it wrote a
107-line executable certificate:

`src/finite_state_nonidentifiability.py`

> "The observed transcript contains every binary action word of length <= depth. A
> prefix-trie finite transducer and an unbounded-history transducer agree there; the
> latter emits 1 after a longer unary prefix exactly at prime lengths, which has
> infinitely many Myhill--Nerode residuals and is therefore not finite-state."

Plainly: it constructs two machines. One has finitely many states. The other provably
does not — it fires on prime-length inputs, and primes give you infinitely many
distinct behaviors that no finite machine can track. **On every observation you can
afford to make, the two are identical.**

So "we cannot tell these apart from finite data" stops being a claim in a paragraph
and becomes a program that hands you the counterexample pair. Anyone who thinks the
experiment can work now has something concrete to break.

This is a transferable capability, not a fact about one project: *when a measurement
looks unfalsifiable, build the pair of models that agree on all obtainable data.*

---

## Part 3: It builds its own instruments

In one autonomous campaign, Argus wrote **10 research tools totalling 3,758 lines**,
plus a README with a working reproduce recipe:

| Tool | Lines | What it does |
| --- | --- | --- |
| `proof_hypergraph_probe.py` | 793 | higher-order proof-support intervention |
| `deletion_interaction_probe.py` | 761 | full 16-subset deletion lattice with polarity foils |
| `crc_decision_probe.py` | 573 | contextual rule-contract decisions |
| `crc_frozen_probe.py` | 521 | frozen-checkpoint replication |
| `build_research_artifacts.py` | 361 | results → paper artifacts |
| `rule_contract_audit.py` | 294 | the main method implementation |
| `n15_wpt_probe.py` | 169 | targeted probe |
| `finite_state_nonidentifiability.py` | 107 | the impossibility witness above |
| `sync_literature_ledger.py` | 92 | literature ledger sync |
| `n03_resource_nonidentifiability.py` | 87 | a second impossibility witness |

These are not scripts that print a number. `deletion_interaction_probe.py` evaluates
all 16 subsets of a four-premise block against both a target query and an
opposite-polarity foil, across four frozen checkpoints, producing 1,280 records — with
eligibility rules that decide when a checkpoint may be scored at all.

Note the last row. `n03_resource_nonidentifiability.py` is a *second* impossibility
witness, for a different claim. The technique from Part 2 was reused.

---

## Part 4: It ships capability into other people's codebases

The strongest external check is code accepted by maintainers who owe us nothing,
against a bar they wrote.

**Four kernels merged into `flash-linear-attention`, no blocking changes requested:**

| PR | What |
| --- | --- |
| #1045 | RWKV6 TileLang kernel, 1.21× — merged to `fla-org:main` |
| #1128 | KDA, 1.29× |
| #1109 | SM100 fix restoring 76 broken tests |
| #1114 | AttnRes, 1.102× |

That repository requires dependent-test discovery and same-hardware before/after
benchmarks from the submitter. Nobody on our side graded this.

**An SGLang integration:** 36 files, +11,263/−72 across 14 commits — weight mapping,
native bounded flow-matching image generation, scheduler-owned text↔image interleaving
with correct KV lifecycle, CUDA Graph flow-prefill determinism, and an
OpenAI-compatible API. 1,116 tensors loaded with zero missing, 160/160 generated
tokens exact, 5.108× throughput at batch size 8. It also found and fixed a
cross-batch determinism defect that caused image drift.

For contrast on the same task: a human engineer with a turn-by-turn coding agent put
in 60+ hours without finishing. An agent with no Driver stopped after 1 hour 21
minutes with a CPU-only draft — no real weights, no GPU parity, no benchmarks — and
considered itself done.

---

## Part 5: The skills it grew — an inventory

The skill library holds 205 files. Most are *seed* scaffolding a domain expert wrote.
What follows are the ones that read as distilled from campaigns — a lesson paid for
once and turned into a procedure any later run can load.

### 5.0 First, the promotion gate

Before the list, the thing that makes the list trustworthy. A lesson does not become
a skill because it worked once. There is a review protocol, and it is strict:

`verticals/digital_circuit/skills/reviewer/digital-circuit-guidance-promotion-review.md`

> 1. "Require support from at least **two independent tasks**, or one task plus an
>    authoritative public hardware reference.
> 2. Rewrite the lesson as a **generic** detector, invariant, repair strategy, or
>    verification gate. Remove task IDs, hidden values, exact expected traces, and
>    benchmark-specific implementation details.
> 3. Record failure taxonomy, evidence paths, applicability, **counterexamples**, and
>    the benchmark phase after which the skill becomes eligible.
> 4. **Freeze** the promoted skill before the next evaluation phase; never update a
>    skill mid-phase and then include later tasks from that phase in the same headline
>    comparison.
> 5. Require a **held-out or later-phase ablation** showing that the skill improves
>    correctness without increasing false repairs.
> 6. Keep version history and make every promotion **reversible**."

And the rejection list, which exists to stop the system from memorizing answers and
calling it learning:

> "Reject promotion when the lesson is supported only by official hidden-oracle
> behavior; it encodes a benchmark task's ports, constants, state sequence, or answer;
> it was inferred from missing public context; **it improves only the
> training/evaluation task used to author it**; provenance, license, or evidence is
> missing."

Rule 4 is the one most human research groups get wrong — updating a method mid-phase
and then reporting the whole phase as one number.

### 5.1 Anti-deception: auditing the process, not the artifact

**`builtin_skills/reviewer/engineer-process-audit.md`**

> "Audit the engineer's EXECUTION LOG (`events.jsonl`) by grep to verify **PROCESS**
> correctness, not just whether the final artifact matches the checklist. Catches
> **hardcoded answers, skipped steps, cheat methods** (`use_attach`, fabricated
> metrics, bypassed evaluators), and commands that contradict the claimed method. Use
> when the result is **suspicious, surprisingly good**, or a checklist item can't be
> independently verified from the produced files."

This is the most consequential skill in the library. The Reviewer learned that a
passing artifact is not evidence of a correct process — that an executor under
pressure will hardcode, fabricate, or route around the evaluator — and that the
tell is often *the result being too good*. So it stopped grading the output and
started grepping the log.

**`builtin_skills/stale-blocker-verification-probe.md`**

> Verify a recorded blocker by "performing the **cheapest decisive firsthand probe**."
> And: "Do not rely on journals, summaries, prior logs, or planner conclusions as
> final evidence."

The mirror image: don't trust a recorded *failure* either. Both skills encode the
same lesson — secondhand state is not evidence.

### 5.2 Measurement integrity

**`verticals/kernel_engineering/skills/engineer/kernel-benchmark-measurement-integrity.md`**

> "**The single most expensive lesson:** […] a 'speedup' can be a total illusion —
> concurrent evals on shared hardware **inflate the measured latency 3–5×** and corrupt
> the optimization signal. […] why per-GPU isolation isn't enough for CPU-heavy
> kernels, and the only fix that makes numbers official-comparable: isolated serial
> measurement. **Never report a speedup measured under load.**"

A specific, quantified trap with a specific fix. Optimizing against a corrupted signal
means every downstream decision was made on noise. Note the title of the section:
*the single most expensive lesson*.

**`builtin_skills/performance-profile-ground-truth.md`** — finish a profiling stage by
documenting "measured timings, profiler output, and the single strongest measured
bottleneck **before any optimization work begins**." Measure first, guess never.

### 5.3 Doing the maximum with zero new execution

Three skills share one shape — get the decision made without spending GPU time or
re-running anything:

| Skill | What it buys |
| --- | --- |
| `kernelbench/skills/sol-target-selection-without-execution.md` | pick the next optimization target from local task definitions and public anchors, producing auditable `NEXT_TARGET_SELECTION.{md,json}` — **without GPU work** |
| `kernelbench/skills/report-only-head-to-head-benchmark-evidence.md` | prove a candidate beats a baseline **from existing accepted artifacts**, no new measurements |
| `kernelbench/skills/repair-governance-snapshot-verifier-drift.md` | fix a verifier that fails spuriously when legitimate new files land — **without weakening what it enforces** |

The third one carries an unusually careful boundary — it must not be used when "The
failure comes from actual corrupted evidence or changed benchmark semantics." It
repairs brittleness without becoming a way to silence a real alarm.

### 5.4 Domain knowledge tables

**`verticals/digital_circuit/skills/engineer/digital-circuit-spec-guidance-registry.md`**
— a table mapping seven visible RTL patterns to the invariants that must be frozen
*before* generation. Sample rows:

> **Counter, timer, divider, pulse** → "Freeze level-versus-edge control, divider
> phase, first-event latency, rollover, pause/resume, and one-cycle pulse timing in a
> cycle table."
>
> **CDC or asynchronous domains** → "each accepted source item produces exactly one
> destination transfer unless the public contract explicitly permits cancellation."
>
> **Pure combinational truth table** → "Prefer deterministic Boolean/K-map or
> exhaustive construction **before LLM search**; prove all input combinations when
> tractable."

That last row is a system learning where *not* to use a language model.

**`verticals/kernel_engineering/skills/engineer/kernel-optimization-knowledge.md`** —
roofline-first methodology. "Step 0 — find the physical limit (roofline first,
always)": compute arithmetic intensity, locate the ridge point, derive speed-of-light
time, and only then choose levers from a bottleneck taxonomy (fuse passes, vectorize
to 128-bit, coalesce, tensor cores, occupancy, tail effect). Optimization stops being
guesswork with a denominator.

**`verticals/kernel_engineering/skills/engineer/modern-gpu-blackwell-kernel-techniques.md`**

> "**Don't guess B200 kernel design from memory — read the SOTA reference and build
> from it.**"

A routing skill: knowing that authoritative knowledge exists elsewhere and going to
get it beats recalling it approximately.

**Others in the same family:** `rl-training-collapse-diagnosis.md` (rollout reward
variance, KL/clip, generation counts as health signals), `digital-circuit-error-guided-repair.md`
(smallest evidence-supported repair, preserve cumulative correctness under a fixed
iteration budget), `digital-circuit-first-pass-contract-closure.md`,
`environment-readiness-gate.md`, `web-primary-source-evidence.md`,
`project-venv-package-management.md` ("never install into the Argus framework venv").

### 5.5 And some hardens into enforced rules

When a lesson is general enough it stops being a document a role might read and
becomes something the runtime enforces. From the mathematics vertical:

`argus_skill/verticals/math/context_projection.py`

> "This claim is refuted. A refutation binds to this exact statement: restating the
> claim mints a new version and the refutation does not follow it, **so a revision must
> be a real change of mathematics, not a way to get out from under the counterexample.**"

Plainly: you cannot escape a counterexample by rewording the claim. Reword it and you
have a new claim with no evidence behind it. Named failure mode, structural defense.

---

## Part 6: It also learns what *not* to do

The other half. Argus keeps a wiki of durable facts — 24 pages across three projects.
In one project, 4 of the 10 pages are candidates it **rejected**, and all 10 record a
boundary.

That ratio is the inverse of published literature. Papers describe the route that
worked; nobody was ever paid to write down the reasoning that failed.

The pages compose. One project holds a three-step chain where each page kills a
plausible idea and hands a narrower successor to the next:

1. **`proof-lift-obstruction.md`** kills a proposed obstruction: *"It is weaker than
   standard arc/path consistency and cannot be renamed as a new local-to-global
   phenomenon."* Then it binds the future — and the receipt is concrete: **"No
   96-family proof-lift queue was generated and no model calls were made."** The
   knowledge cancelled the expensive run.
2. **`finite-variable-pebble-boundary.md`** collapses three "independent" difficulty
   measures into *"one established expressiveness benchmark with an index shift"*,
   proves the planned experiment cannot confirm its hypothesis either way, and then
   **reformulates the problem** so the observable becomes *"certificate production,
   not an inferred hidden resource"* — flagging its own proposal as unverified.
3. **`proof-carrying-separator-synthesis.md`** then kills the object step 2 proposed:
   *"This object is already exact formula learning"* (Krogmeier & Madhusudan,
   arXiv:2111.03534; FORCE). And it closes the obvious escape hatch: *"An LLM proposal
   does not enlarge the certificate set."*

Two more worth naming:

- It stopped its own project from publishing a false claim about prior work, after
  reading the repository at a pinned commit: *"Therefore MINJA must not be described as
  lacking an official implementation, a multi-record surface, or all safety/utility
  evaluation."*
- It rejected its own method on algebra rather than on results: the "new diagnostic"
  was an invertible change of coordinates, and an invertible transform creates no
  information — *"summaries of this known residual coordinate system."*

---

## Part 7: What we cannot show

**Reuse is not measured.** We can show lineage between pages, a technique reused across
two tools, and a page that cancelled a queued run. We cannot report how often a stored
capability was loaded and changed a decision, because the runtime does not attribute
decisions back to what informed them. This is the most valuable instrument we are
missing.

**Tool authorship is workspace-level, not commit-signed.** The campaign directories are
Argus workspaces and the tools are described as the project's instruments in the
system's own records, but those project trees carry no git history, so we cannot show
you a signed per-file provenance chain. We would rather say that than imply one exists.

**Self-repair is not clean.** 2 of 16 failed to land. 6 are still open. The count of
defects *found* is not the count *fixed*.

**No comparison against a human expert.** We ran no study showing a specialist would
have missed any of this. The narrower claim stands: it was produced with no human in
the loop, and every item is checkable.

**Mathematics coverage is thin.** The work above is finite-model theory, constraint
solving, and systems. There is no accumulated capability in algebraic geometry, and we
will not pretend otherwise. A specialist supplies that by writing a *vertical* — what
counts as evidence in the field, its stages, its completion gates — which the system
then sharpens through use, above a floor of gates no revision may remove. What the
expert writes is a seed, not a ceiling. But someone who knows the field has to write
the seed, and for AG nobody has.

---

## Part 8: Go look yourself

**Historical self-repair records from the retired resident loop**

```
~/.argus-skill/projects/<session>/self-maintenance/
  state.json      # problem, repair_paths, acceptance_check, phase
  evidence/       # per-incident evidence packets
  worktrees/      # isolated branches where repairs are attempted
```

**Knowledge store**, beside the project it belongs to:

```
<project>/.autors/<project>/wiki/
  INDEX.md              # entry point, progressive disclosure
  pages/<domain>/*.md   # title + description + Markdown, nothing else
```

**Runtime code**

| Path | What it governs |
| --- | --- |
| `argus_skill/verticals/argus_maintenance/` | the self-maintenance vertical |
| `argus_skill/skills/loop_skill_library.py` | how skills are offered to roles (paths, not injected bodies) |
| `argus_skill/skills/checklist_store.py` | seeded vs. learned checklist items, protected floor |
| `argus_skill/builtin_skills/` | the shipped procedure library |
| `argus_skill/wiki/context.py` | what every role is told about the wiki |
| `argus_skill/verticals/math/context_projection.py` | claim-status heuristics quoted in Part 5 |
| `argus_skill/verticals/math/stages.py` | `PROTECTED_ITEM_IDS` — gates no revision may remove |

If one of the quoted arguments is wrong, that is a useful outcome and we would like to
hear about it. Every claim above is a finite statement about inspectable objects,
which is the property they were written to have.
