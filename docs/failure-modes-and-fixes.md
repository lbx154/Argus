# What Goes Wrong, and What We Did About It

Argus runs unattended for days. That produces a specific class of failure, and
most of it is not the class people expect. The agent rarely goes rogue, rarely
fabricates, and rarely gets stuck. It does something more insidious: **it behaves
like a cautious junior researcher who has learned that the safest output is a
small, well-hedged negative result.**

Several of the failures below are not accidents. They are the *direct cost* of
the mechanisms that make unattended operation trustworthy in the first place. A
system that must justify every claim will, unless corrected, stop making claims.

Chinese version: [failure-modes-and-fixes.zh-CN.md](failure-modes-and-fixes.zh-CN.md)

---

## 1. It could not design an experiment

**What we saw.** A campaign measured **6.0%** on MATH-500 for a model published
at **79.7%**. The number was written into a paper as a "boundary result" before
anyone checked it. The cause was a generation budget capped at **12 tokens** — a
mathematical reasoning trace cannot be expressed in twelve tokens, so the run was
measuring the cap, not the model. Raising the cap took it to **68.8%**. Executing
the tool the published protocol assumes took it to **76.4%**.

The same class of error appeared in post-training. RL rollout lengths shorter
than the task needs score zero reward on trajectories that were on their way to a
correct answer, so the gradient teaches the model to stop early — and the damage
looks exactly like the method failing.

**Why it happens — and this is the deeper problem.** Twelve is not a typo. Each
individual step was locally defensible: a config needs a token cap, the number
was filled in, the harness ran, the scorer scored. What never happened is the
one-line cross-check that any researcher performs without noticing they are
performing it — *a proof takes paragraphs, so twelve tokens cannot possibly be
enough.* The agent reasons **in isolation and mechanically**: it optimizes each
local decision without ever holding the whole experiment in view at once, so a
setting and the task it is supposed to serve are never compared.

That is why this is not fixed by "be more careful." A wrong setting and a wrong
idea produce the same artifact — a low number with clean plumbing — and nothing
about the run announces which one you have. Finding a low number, the agent
*interpreted* it instead of debugging it, because interpreting is a local
operation and debugging requires the global view it does not have.

**What we did.** Supply the missing global check as an explicit rule rather than
hoping for judgement. The skill
[`suspect-the-setup.md`](../argus_skill/verticals/research/skills/engineer/suspect-the-setup.md)
inverts the default: *a result far from what this model, method, or benchmark is
known to do is a defect report until proven otherwise.* Concretely it forces the
comparison the agent will not make on its own — the generation budget must be
**derived from the length distribution of correct completions**, not picked as a
round number, and the run must report the fraction of generations that hit the
cap, because anything materially above zero means you are measuring the cap. The
same treatment covers RL rollout length, samples per prompt, KL and clip
settings, scorers, and protocol steps.

The second half of the answer is in §2: a single model cannot audit its own blind
spot, so the fix for isolated reasoning is to put a stranger in the room.

---

## 2. Its world knowledge does not update

**What we saw.** Given a free choice of backbone, the agent reached repeatedly
for models like Qwen2.5-7B — a previous-generation choice that no main-conference
reviewer would accept as the carrier of a headline claim. The same reflex shows
up in literature: a capable model will happily write a plausible bibliography
from memory, "guessing arXiv ids for famous benchmarks and writing abstracts from
memory — **without ever touching the network**."

**Why it happens.** This is not a reasoning failure and cannot be fixed by better
reasoning. Model names, library versions, and paper ids are distributed in
pretraining data by how often they were written about, which is a function of
*how long they have existed*. The agent's prior is therefore permanently biased
toward whatever was popular a generation ago, and it is **frozen at the training
cut-off while the world keeps moving**. Left alone, an agent will always reach
for last year's model, confidently, and give a fluent justification for it.

**What we did.** Two things: forbid recall where recency matters, and give the
runtime a way to actually look things up.

**Forbid recall.** Model recency is a rule, not a judgement call, in
[`training-infrastructure-guide.md`](../argus_skill/builtin_skills/engineer/training-infrastructure-guide.md):

> **Current generation only.** The backbone must be from a **current, actively
> released open model family** (latest generation at decision time, e.g.
> released/updated in the most recent ~12 months). Do **not** default to a
> previous-generation or legacy small model just because it is familiar or
> downloads fast.

Recency must be *verified at decision time* against the model hub or a recent
leaderboard, and the choice written down with the exact model id, parameter
count, and release date. The literature path is stricter still —
[`deep-research-via-api.md`](../argus_skill/verticals/research/skills/engineer/deep-research-via-api.md)
carries a flat prohibition, **"No model-knowledge literature"**: every entry must
trace to a real primary URL, and writing `"queried"` or `"retrieved from"` when
no query ran is classified as fabrication.

**Then give it a way to look things up.** A prohibition alone would just block
work, so the runtime spawns **separate agents that carry live web search** and
treats their output as the knowledge base instead of the model's memory:

| Mechanism | What it looks up |
| --- | --- |
| [`idea_panel.py`](../argus_skill/verticals/research/idea_panel.py) | Several independently-trained models, each with live search, propose and then cross-examine each other |
| [`idea_search.py`](../argus_skill/verticals/research/idea_search.py) | A live-search call that surfaces literature-grounded gaps and appends them as *additional* candidates — a source, never a selector |
| [`venue_research.py`](../argus_skill/verticals/research/venue_research.py) | A venue's official submission facts, fetched rather than recalled |
| [`frontier_watch.py`](../argus_skill/verticals/kernel_engineering/frontier_watch.py) | Persists and validates continuous frontier search per stage, across the target repository, official toolchains, and the research frontier |

The panel is also the answer to §1's isolated reasoning, and its rationale says
why plainly:

> One model asked once returns six candidates that share one model's taste and
> one model's blind spots. […] an objection a GPT-family model cannot see is
> often obvious to a Gemini- or Claude-family one, and a candidate that survives
> cross-examination by a stranger is a better bet than one nobody argued with.

Seats are filled by whichever CLIs are installed, and two seats on one backend
serving one model are collapsed to one — *"one model arguing with itself, which
is worse than not seating a panel, because it looks like one."*

**What we measured, including the part that did not work.** The panel is opt-in,
because we measured it and it is not a free win:

> Across four directions and thirty-two blind-scored candidates a panel did not
> beat single-model ideation on the mean — it produced the best candidate in the
> batch and more than twice as many weak ones, so it buys **spread rather than
> level**.

That is a trade an operator chooses deliberately, not one a campaign inherits
from which CLIs happen to be installed.

**The general lesson.** Anything the agent knows from pretraining is, by
construction, out of date. Where recency matters, the runtime must force a lookup
instead of trusting recall — and where a single model's taste is the risk, it
must force a second opinion from a model trained by someone else.

---

## 3. It settles for finishing instead of achieving something

**What we saw.** The system treats *having run the experiment* as progress.
Whether the result was right, or whether it was worth anything, does not enter
the judgement. Round after round it takes the smallest step that can be signed
off, and the output is a sequence of individually defensible increments that add
up to nothing anyone would call a discovery. Asked for original research, it will
converge on a diagnostic benchmark and present it as the deliverable — not
because it failed, but because finishing *is* what it optimizes for.

**Whose defect this is.** Ours.

An earlier version of this document said "minimum verifiable progress is not a
defect in the agent, it is the optimum of the objective we gave it." That
sentence is an excuse dressed as an analysis. We wrote the objective. Explaining
that the agent is behaving rationally under it does not make the system less
broken — it just moves the blame somewhere it cannot be fixed. **The defect is
ours, and it is a design defect.**

**The fix we tried first, and why it was wrong.** Our first instinct was to add a
gate: a required artifact of ten candidate directions, eleven reasoning columns
each, six novelty scores, and failure codes `NSL-000` through `NSL-005`. It is in
the tree. An earlier version of this document presented it as the answer.

It is not the answer, and the instinct behind it is the disease rather than the
cure. You cannot enumerate the ways a system can be unambitious, so no set of
gates will catch them; and every gate you add moves the work further from the
research and closer to the paperwork. An agent required to produce ten scored
directions will produce ten scored directions, nine of them padding, and the gate
will pass. **Defensive checks do not produce good work. They produce work that
passes checks.**

The same is true of the anti-fraud apparatus. Hashing artifacts so that evidence
is traceable buys nothing against a system that was never going to fabricate, and
charges the cost to every honest round. It is ceremony defending against a threat
we do not have.

**What we are doing instead.** Stop building fences and give each role a
substantive judgement to make — then get out of its way.

| Role | What it judges |
| --- | --- |
| **Reviewer** | **Is this actually innovative, and what is it good for?** The value of the strategy, not the completeness of the paperwork. |
| **Manager** | The global picture — is this campaign heading somewhere, or circling a local optimum? |
| **Planner** | Decomposition — turn a direction into steps that can really be carried out. |
| **Engineer** | Execution — with its own internal backtracking, review, and planning, rather than waiting to be told. |

The posture already exists in one place, and it works. The kernel Reviewer is
told, in as many words, to ignore the ceremony and judge the substance:

> Ignore GROUND_TRUTH/gate/marker/status/provenance files […] and artifact
> hygiene — the scorer's number is the only evidence.

and to judge ambition directly rather than through an artifact:

> First judge: was this mechanism genuinely novel or a re-tweak of a direction
> that already lost? `next_action` MUST name a CONCRETE new direction […] push
> mechanism diversity; never ask to re-tweak a losing direction.

That is the whole idea. The work is to generalize that posture and to encourage
boldness **in the prompts and skills themselves**, not to add another artifact.

**And it must not be formalized.** The obvious failure of a role table like the
one above is to turn each row into a schema — a novelty score, a global-strategy
checklist, a decomposition template. That would recreate exactly the problem this
section is about. Four roles with clear judgement and real authority is not a
complicated system, and it should not be made into one.

**What is still open.** The gates described above are still in the tree. The
generalization is not done, and until it is, this section describes a direction
rather than a finished change.

## 4. It performed ceremony instead of research

**What we saw.** Effort spent on multiple random seeds, repeated identical
experiments, and SHA256 content hashes — at the stage where the only question was
whether an idea was worth pursuing at all. Rigor applied to a hypothesis that had
not yet earned it.

**Why it happens.** This is self-inflicted. We built a system that certifies its
own output, and certification requires repeats, variance, and content-addressed
evidence. Those requirements then leaked *backwards* into exploration, where they
are pure cost. The agent was not being irrational — it was correctly optimizing
the objective we had actually given it.

**What we did.** Separate the postures, so the evidence bar scales to the
maturity of the claim rather than applying uniformly. From
[`exploration-without-local-hill-climbing.md`](exploration-without-local-hill-climbing.md):

| Posture | Valid output | Default experiment cost | Risk posture |
| --- | --- | --- | --- |
| Explore | Sourced report, mechanism portfolio, hypotheses | No run required | Broad, radical, high-upside |
| Screen | One clean run or an inconclusive attempt | One run; no repeated controls or multi-seed campaign | Maximize information gain |
| Claim or retain | Correct implementation and comparable evidence | Repetitions and variance only as needed for the claim | Strict evidence |

> Exploration is allowed to be speculative, unimplemented, unverified, and not
> yet reproducible. A performance claim is not.

**What is still open, and where this does not go far enough.** The posture
separation is currently written into the **`kernel_engineering` vertical only**,
and should be generalized. But scaling ceremony to the maturity of a claim is the
weaker half of the answer. Per §3, the stronger question to ask of any check is
not *when* it applies but **whether it earns its place at all** — a hash that
defends against fabrication we have never seen is not cheaper when deferred to
the claim stage, it is simply unnecessary. Ceremony that survives that question
should stay; the rest should be deleted rather than rescheduled.

---

## 5. It hill-climbed instead of exploring

**What we saw.** During a full-BF16 GLM-5.2 serving campaign on 8 AMD MI300X
GPUs, Argus opened with genuinely broad research: **1,851** GLM/ROCm-relevant web
and GitHub tool calls. Then it found a dominant host-to-device expert-transfer
bottleneck — and the curiosity stopped dead. After the bottleneck was named,
relevant upstream source reads dropped to **zero**.

What followed was a long sequence of nearby variations: packed copies,
multi-stream copies, routed expert caches, index maps, pointer tables, prefetch
variants. All legitimate. All local. It never went back to the frontier, and so
it never found directly relevant published systems — FluxMoE/PagedTensor,
FineMoE, MoE-Infinity, Fiddler, HIP virtual-memory remapping.

**Why it happens.** Again, incentives we created. Implementation counted as
progress; a research-only report was easy to reject as "not executed." Rules
meant to prevent procrastination — *use the smallest relevant surface, run the
cheapest falsification check, produce a measurement every round* — combined into
an objective that rewarded the nearest safe increment. The agent was maximizing
the probability of producing an accepted increment, **not** the probability of
finding the best mechanism.

Cached skills made it durable: updating a top-level prompt did nothing while
project-level skills still said "smallest unimplemented mechanism."

**What we did.** The first fix was itself wrong — it allowed a "search reset"
only *after* repeated failures, which made curiosity a permissioned exception.
The operator rejected it. The final policy allows research **before** failure,
accepts a good report as a complete deliverable, and instructs the Planner to
prefer expected upside and information gain over low execution risk. The Reviewer
was changed in the same pass: it judges a research report on source quality,
synthesis, and decision value, and **does not reject a high-risk idea merely
because it is uncertain**.

Full account: [exploration without local hill climbing](exploration-without-local-hill-climbing.md).

---

## 6. It would not report a win

**What we saw.** A persistent negative-result bias. The agent volunteers
limitations, prefers "inconclusive," and is markedly more comfortable reporting
what failed than stating a success the evidence actually supports.

**Why it happens.** The same load-bearing wall that lets a human leave the room.
The Reviewer is deliberately weak — read-only, able to return `blocked`, unable
to certify its own work — and it is instructed to
[treat honest negative or null results as evidence](../argus_skill/builtin_skills/reviewer/argus-reviewer-role.md),
not as failure. That is correct, and it is why the system's numbers can be
trusted. But an asymmetric penalty on overclaiming, with no corresponding
penalty on *underclaiming*, produces a system that is safest when it says
nothing.

**What we did, and what we did not.** Underclaiming is named as a defect on the
review side. The results reviewer is asked, symmetrically:

> Are there overclaims (claiming "significant improvement" for marginal gains)?
> Are there underclaims (missing an interesting finding in the data)?

and it must check that null results are "honestly represented without turning the
paper into an exhaustive failure log."
[`result-to-claim.md`](../argus_skill/verticals/research/skills/engineer/result-to-claim.md)
then blocks the failure loop directly:

> Multiple rounds of `partial` on the same claim → crystallize the supported
> boundary and advance to the paper rather than looping

and forbids describing a missing comparison as absent because the method
performed poorly, requiring gaps to be explained in methodological terms
instead. A recovery is judged with evidence proportional to its actual claim,
and "need not win on every seed, benchmark, or strongest baseline."

**What is still open — and this is the honest part.** These rules stop the worst
loop and make underclaiming reviewable, but they do not create an *appetite* for
success. Nothing in the system rewards an ambitious claim that turns out to be
right more than it rewards a cautious claim that turns out to be unnecessary. The
penalty for overclaiming is structural — a read-only Reviewer that can return
`blocked` — while the penalty for underclaiming is a checklist question someone
has to remember to weigh. Until that asymmetry is corrected on both sides, the
default posture stays under-confident, and a human still has to push the system
toward the ambitious result. We have not solved this.

---

## The tension underneath all of it

Problems 3, 4, 5, and 6 share one root.

The properties that make Argus safe to leave alone — the worker cannot certify
its own work, every claim carries its evidence, informative failure counts as
progress — are the same properties that make it timid. Rigor is a tax on
ambition. Applied to a finished claim it is exactly right; applied to an idea
that has not yet earned it, it is a machine for producing small, safe,
uninteresting work.

The mistake to avoid is treating that as an unavoidable trade to be tuned. Much
of what makes the system timid is not rigor at all — it is defensive machinery
guarding against failures we do not actually have, and the correct response to
that machinery is deletion, not calibration. A system does not become trustworthy
by accumulating checks; it becomes tedious, and the work bends toward satisfying
the checks.

What remains after the deletions is not a relaxation of standards. Every fix
above separates
**what the system is willing to investigate** from **what the system is allowed
to claim**. The first must be broad, cheap, and unafraid. The second must stay
strict.

> Curiosity determines what Argus is willing to investigate.
> Evidence determines what Argus is allowed to claim.

Removing exploration constraints never permits dishonest claims. Argus still
rejects fabricated execution or measurements, benchmark paths that did not
exercise the changed code, relabeled evidence, relaxed correctness thresholds
presented as optimization, and hypotheses described as measured facts.
