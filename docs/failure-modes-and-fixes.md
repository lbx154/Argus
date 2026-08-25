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

**Why it happens.** A wrong setting and a wrong idea produce the same artifact: a
low number with clean plumbing. Nothing about the run announces which one you
have. The agent, finding a low number, interpreted it instead of debugging it.

**What we did.** A skill,
[`suspect-the-setup.md`](../argus_skill/verticals/research/skills/engineer/suspect-the-setup.md),
inverts the default: *a result far from what this model, method, or benchmark is
known to do is a defect report until proven otherwise.* It requires the
generation budget to be derived from the length distribution of correct
completions rather than picked as a round number, and requires reporting the
fraction of generations that hit the cap — anything materially above zero means
you are measuring the cap. It carries the same treatment for RL rollout length,
samples per prompt, KL and clip settings, scorers, and protocol steps.

---

## 2. It kept picking outdated models

**What we saw.** Given a free choice of backbone, the agent reached repeatedly
for models like Qwen2.5-7B — a previous-generation choice that no main-conference
reviewer would accept as the carrier of a headline claim.

**Why it happens.** This one is not a reasoning failure, and it cannot be fixed
by better reasoning. Model names are distributed in pretraining data by how often
they were written about, which is a function of *how long they have existed*. The
agent's prior is therefore permanently biased toward whatever was popular a
generation ago. Left alone, an agent will always reach for last year's model,
confidently, and give a fluent justification for it.

**What we did.** Model recency is enforced as a rule rather than left to
judgement, in
[`training-infrastructure-guide.md`](../argus_skill/builtin_skills/engineer/training-infrastructure-guide.md):

> **Current generation only.** The backbone must be from a **current, actively
> released open model family** (latest generation at decision time, e.g.
> released/updated in the most recent ~12 months). Do **not** default to a
> previous-generation or legacy small model just because it is familiar or
> downloads fast.

Recency must be *verified at decision time* against the model hub or a recent
leaderboard, not recalled. The choice must be written down with the exact model
id, parameter count, and release date, and if a small model is chosen
deliberately the reason must be a research reason — not "it was easier to train."
A stale backbone may remain a compatibility baseline, but it cannot carry the
main empirical claim when a current generation exists.

**The general lesson.** Anything the agent knows from pretraining is, by
construction, out of date. Where recency matters, the runtime must force a lookup
instead of trusting recall.

---

## 3. It performed ceremony instead of research

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

**What is still open.** This posture separation is currently written into the
**`kernel_engineering` vertical only**. The research vertical has a related rule
— a recovery is judged "with evidence proportional to its actual claim" and "need
not win on every seed" — but the explicit *no ceremony during exploration* policy
has not been generalized to every vertical. It should be.

---

## 4. It hill-climbed instead of exploring

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

## 5. It would not report a win

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

Problems 3, 4, and 5 share one root, and it is not a bug we can simply remove.

The properties that make Argus safe to leave alone — the worker cannot certify
its own work, every claim carries its evidence, informative failure counts as
progress — are the same properties that make it timid. Rigor is a tax on
ambition. Applied to a finished claim it is exactly right; applied to an idea
that has not yet earned it, it is a machine for producing small, safe,
uninteresting work.

So the fixes above are not about relaxing standards. Every one of them separates
**what the system is willing to investigate** from **what the system is allowed
to claim**. The first must be broad, cheap, and unafraid. The second must stay
strict.

> Curiosity determines what Argus is willing to investigate.
> Evidence determines what Argus is allowed to claim.

Removing exploration constraints never permits dishonest claims. Argus still
rejects fabricated execution or measurements, benchmark paths that did not
exercise the changed code, relabeled evidence, relaxed correctness thresholds
presented as optimization, and hypotheses described as measured facts.
