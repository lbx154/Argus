---
name: "Training infrastructure: choosing, standing up and tuning it"
description: "Choose the training or large-inference stack for the selected method from live sources and local measurement rather than from remembered names; stand each shortlisted candidate up at a pinned revision, profile one real step, decide engine use from where that step spends its time, then tune one factor at a time from the official recipe. The answer is recorded as a dated project decision that later projects re-verify."
---

# Training infrastructure: choosing, standing up and tuning it

Use this only when the selected method trains a model or runs inference at a
scale where the framework matters. Nothing here changes the frozen model, the
data or the comparison protocol. The project environment skill in the global
library (`project-venv-package-management.md`) still governs interpreters,
installation and caches, and no ML stack is set up for work that does not need
one.

## Why the answer is produced, not remembered

Framework, engine and model names in memory are dated. The libraries that
dominate a task class change faster than any training corpus, their pins drift
against local drivers, and the project that migrated away from a stack last
quarter is invisible to recollection. So no name in this skill or in memory is a
recommendation. The current answer is produced at project time from live
sources and local measurement, and written as a dated project skill,
`engineer/<task-class>-infrastructure-decision.md` (the round prompt describes
its shape), so that the Reviewer can check every date and number against a
cached source or a run log, and a later project on the same task class and
hardware can start from the record instead of repeating the work. When such a
record exists and its stated re-verify date has not passed, start from it; when
the task class or the hardware changed, it no longer applies.

## Survey the landscape from live sources

Fetch every page with `python -m argus.tools.web_source <URL>`; it stores the
text under `.argus/sources/` with an access date, and that cached path is cited
beside every fact taken from it. A date asserted from memory has nothing
behind it and cannot be re-verified. Sources, roughly in order of how much they
tell you:

- repository search sorted by recent push, with the query string and date
  window recorded so the survey itself can be reproduced;
- for each candidate, the latest release or tag date and the last-commit date
  read from the repository, not from a blog post or a README badge;
- recent papers on the task class (`engineer/sources-and-citations.md` covers
  the search) and the code each releases: the stack a paper actually ran on is
  stronger evidence than the stacks it mentions;
- the official documentation of every shortlisted candidate, for the
  capabilities the evidence card asks about;
- the released baseline's own stack: when the comparison baseline ships code,
  its framework is a candidate by default, because matching it removes one
  confound from the comparison.

Successor discovery is what makes the survey current rather than a
confirmation of what you already knew. For every framework name you remember
for this task class, fetch its release and last-commit dates first; then look
in READMEs, issue trackers and recent papers for projects that benchmark
against it, fork it or say they migrated away from it, and make each such
project a candidate. Repeat once for the newly found names. A remembered name
that was never fetched is not a candidate.

Record recent activity (commits, releases, closed issues) over a window you
state, and treat a long silence as something to explain on the card rather than
as an automatic exclusion: a small stable library that finished its job is a
different case from an abandoned one.

Write one evidence card per candidate, rejected ones included: official and
documentation URLs with access dates and cached paths; release and last-commit
dates; supported algorithms for this task class; native multi-turn or agentic
rollout support against the custom loop you would otherwise write; integrated
inference engines and whether they colocate with the trainer; support for the
frozen model's architecture, PEFT and quantization; whether tool observations
are masked out of the policy loss; turn-level versus token-level advantage
assignment; CUDA and torch pins against the local driver and the allocated
hardware; license; and the nearest official example to this task, as a path at
a SHA. The card exists so that every rejected candidate carries an exclusion
reason that points at a field (a pin the driver cannot satisfy, no loss masking
for tool observations) rather than at a feeling.

Shortlist the candidates worth standing up: two or three is usual, since fewer
leaves nothing to compare and more costs more device time than the comparison
returns. Record every card and query in the decision record, then continue.
"No material update found" is a legitimate outcome only when the record lists
the queries, windows and dates that were checked. When fetches fail, record the
survey as unverified with the failed URLs, tell the Reviewer, and continue; an
offline survey is a documented limitation, not a licence to fall back on
memory.

## Stand each candidate up and measure one real step

Documentation claims become local facts only on the allocated device. For each
shortlisted candidate:

- create an isolated environment (RL stacks carry incompatible pins, and two
  candidates in one interpreter corrupt both measurements), clone at a pinned
  SHA under `third_party/<name>/`, and record the SHA, the release it
  corresponds to and the clone date;
- run the official example nearest the task, shrunk in steps, batch and
  allowance but on the executed path: the same trainer, objective, engine and
  launcher the full run would use, because a pilot that takes a different path
  measures a different system;
- launch it through the durable runner so the logs are attributable:
  `python -m argus.tools.subagent submit ... --intent 'stand-up pilot: <candidate>@<sha>'`;
- keep the effort proportional to what a stand-up can tell you. A candidate
  that does not reach one valid update after its pin conflicts and errors have
  been worked through is recorded with the exact error text and the install
  wall time, not fixed indefinitely: the point is to compare candidates, and a
  stack that resists standing up has already said something about itself.

For the first real training step after warmup, record a phase table: wall time
in startup and compile, generation, environment or tool execution, log-prob
scoring, backward, weight sync and idle, alongside rollout and end-to-end
tokens per second, time to first valid update, device-level peak memory
counting colocated engine processes, utilization, truncation fraction, the
fraction of groups with reward contrast, and a flag when another workload
overlapped the measurement. The table exists because the dominant phase decides
the architecture: a generation-dominated step is the case for a separate
rollout engine, a backward- or sync-dominated step is not, and an
environment-dominated step asks for tool parallelism before anything else.
Deciding engine use before seeing where the time goes is guessing.

When an inference engine is a candidate, run the same shrunk example with the
engine on and off on identical prompts, seeds and device. Engine correctness is
agreement between the engine's and the trainer's log-probabilities on the same
sampled tokens: report the importance-ratio distribution and the fraction
outside the clip range. Identical rewards are not the test, because two engines
can agree on rewards while disagreeing on the probabilities the gradient is
computed from, and that disagreement is what makes an off-policy correction
silently wrong.

From the measured step time derive GPU-hours per valid update and steps per day
under the allocated budget, record one row per candidate with the runner task
id, run directory and device, choose, and state what would change the choice.

## Tune from the official recipe, one factor at a time

Copy the chosen framework's current example configuration nearest the task from
`third_party/<name>/` at the pinned SHA into the project, with a provenance
comment at the top naming the source path, SHA and copy date. Every later
change is a readable diff against that file. A configuration written from
memory has no anchor, and when it fails nobody can tell whether the method or
the recipe is at fault.

Derive the task-bound knobs from a dev pool disjoint from the held-out
evaluation set: the trajectory allowance from a high percentile of
correct-solution lengths counting tool observations as tokens, the
tool-iteration cap from the iterations correct solutions take, and reward
reachability confirmed by running the reward and extraction path on known
correct and incorrect examples before any training step.

Fix the pilot harness (prompts, seeds, device, step count) and change one
factor per pilot, so that a change in the learnability signals can be
attributed to something. Learnability knobs come first (group size, allowance,
reward normalization) because nothing else matters while the signal is dead;
learning rate, KL coefficient and clip range are touched only when the
gradient-norm, KL or entropy traces ask for it; throughput knobs (batching,
engine memory fraction, parallelism) come last and must leave the objective
unchanged, which a pilot shows by unchanged loss terms. Run pilots under the
durable runner's supervised mode with `engineer/rl-training-collapse-diagnosis.md`
as the reference for reading the traces, and record one row per pilot (factor,
value, contrast fraction, gradient-norm and KL ranges, seconds per step, peak
memory, verdict) in the decision record. Put a provenance comment beside each
changed value so the diff still explains itself a month later.

Stop tuning when two consecutive pilots on the same factor move the
learnability signals by less than their pilot-to-pilot noise, or when the budget
reserved for tuning is spent, and record which. Before the first full run,
write an escalation note in `RESEARCH_NOTES.md` stating the reward contrast
seen in N of M groups over K pilot steps, seconds per step times planned steps
against the budget with a stated margin, peak memory at the maximum allowance,
that checkpoint save and resume were exercised on the pilot, the evaluation
cadence, and that held-out data was untouched by every pilot. The Planner and
Reviewer read this note as evidence of whether the full run is worth its cost;
it is not a form to fill in.

## While the run is live

Keep the user-selected model and the frozen baseline protocol; substitute only
within existing authority and disclose any consequential change. Reuse the
framework's own logging, checkpoints and launcher; a custom loop is justified
only by the method or an unsupported operation, and then it is validated
against a trusted reference on a small case. Submit through the durable runner
with disjoint allocations and keep the executable configuration, command,
device visibility and evaluator output with the run outputs. Before
interpreting any result, confirm that the intended path ran. For
policy-gradient health read `engineer/rl-training-collapse-diagnosis.md`; SFT
and offline DPO need their own objective-specific checks. Infrastructure work
ends when the experiment's decisive readiness check passes; no separate
infrastructure report is written.
