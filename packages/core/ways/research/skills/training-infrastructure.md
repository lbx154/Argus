# Training infrastructure: choosing, standing up and tuning it

Use this only when the selected method trains a model or runs inference at a
scale where the framework matters. Nothing here changes the frozen model, the
data or the comparison protocol. The project-local environment of
`experiment.md` still holds, and no ML stack is set up for work that does not
need one.

## Why the answer is produced, not remembered

Framework, engine and model names in memory are dated. The libraries that
dominate a task class change faster than any training corpus, their pins drift
against local drivers, and the project that migrated away from a stack last
quarter is invisible to recollection. So no name in this skill or in memory is a
recommendation. The current answer is produced at project time from live
sources and local measurement, and written down as a dated decision record,
`INFRASTRUCTURE.md` in the workspace, so that the Reviewer can check every date
and number against a cited source or a run log. When such a record exists and
its stated re-verify date has not passed, start from it; when the task class or
the hardware changed, it no longer applies.

The record opens with one line, `Surveyed <date>: <task class> on <hardware>;
re-verify after <date> or when task class or hardware changes`, then holds, in
order: the question and the task class; the survey date and hardware; the
sources (URL, access date); the candidates, rejected ones included; the
stand-up and profile of each; the recipe and tuning rows; the decision; the
command that re-measures it; what it stays valid for; the working commands;
and the pitfalls a run verified. On a refresh, replace the current content and
add one dated line to a History section at the end.

## Survey the landscape from live sources

Fetch every page with your own web tools, and cite its URL and access date
beside every fact taken from it. A date asserted from memory has nothing
behind it and cannot be re-verified. Sources, roughly in order of how much they
tell you:

- repository search sorted by recent push, with the query string and date
  window recorded so the survey itself can be reproduced;
- for each candidate, the latest release or tag date and the last-commit date
  read from the repository, not from a blog post or a README badge;
- recent papers on the task class (`sources-and-citations.md` covers
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
documentation URLs with access dates; release and last-commit
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
- launch it detached with its own log and exit-code file named after the
  candidate (`<candidate>@<sha>`), so the logs are attributable;
- keep the effort proportional to what a stand-up can tell you. A candidate
  that does not reach one valid update after its pin conflicts and errors have
  been worked through is recorded with the exact error text and the install
  wall time, not fixed indefinitely: the point is to compare candidates, and a
  stack that resists standing up has already said something about itself.

For the first real training steps after warmup, time each phase of the step
(generation, forward and backward, optimizer, synchronization, environment), or
keep the framework's own per-phase timing lines, and compute from them each
phase's share of the step, generation tokens per second, and how much faster
the step could be if one phase took no time. Record beside it the time to
first valid update, device-level peak memory counting colocated engine
processes, the truncation fraction and the fraction of groups with reward
contrast. The dominant phase decides the architecture: a generation-dominated
step is the case for a separate rollout engine, a backward- or sync-dominated
step is not, and an environment-dominated step asks for tool parallelism before
anything else. Deciding engine use before seeing where the time goes is
guessing.

When an inference engine is a candidate, run the same shrunk example with the
engine on and off on identical prompts, seeds and device. Engine correctness is
agreement between the engine's and the trainer's log-probabilities on the same
sampled tokens: report the importance-ratio distribution and the fraction
outside the clip range. Identical rewards are not the test, because two engines
can agree on rewards while disagreeing on the probabilities the gradient is
computed from, and that disagreement is what makes an off-policy correction
silently wrong. An engine that does not install into the pinned versions only
says the versions must change or the engine needs its own environment; it is
not a reason to stay on native generation.

From the measured step time derive GPU-hours per valid update and steps per day
under the allocated budget, record one row per candidate with its run
directory, device and the command that re-measures it, and choose.

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
unchanged, which a pilot shows by unchanged loss terms. Read pilot traces with
`rl-training-health.md` as the reference, and record one row per pilot (factor,
value, contrast fraction, gradient-norm and KL ranges, seconds per step, peak
memory, verdict) in the decision record. Put a provenance comment beside each
changed value so the diff still explains itself a month later.

Stop tuning when two consecutive pilots on the same factor move the
learnability signals by less than their pilot-to-pilot noise, or when the budget
reserved for tuning is spent, and record which. Before the first full run,
write an escalation note in `RESEARCH_NOTES.md` stating the reward contrast
seen in N of M groups over K pilot steps, seconds per step times planned steps
against the budget with a stated margin, peak memory at the maximum allowance,
bytes written per step times planned steps against the free disk, that
checkpoint save and resume were exercised on the pilot, the evaluation
cadence, and that held-out data was untouched by every pilot. The Planner and
Reviewer read this note as evidence of whether the full run is worth its cost;
it is not a form to fill in.

## What a training run keeps

Keep in full what the final evaluation rests on: configuration, code revision,
checkpoints at the protocol's save points, the evaluation inputs and
predictions. For the training stream, keep what reproduces it rather than every
batch: the generator version and configuration, per-step seed or task index and
the scalar record of each step, plus a sample of actual inputs (the first steps,
a regular stride, the last steps, and every step that failed a check). Show on
that sample that regenerating from the seed reproduces the saved arrays before
relying on regeneration; when a stream cannot be regenerated exactly, keep the
raw inputs it needs and say so. One project saved every step's full support and
query arrays, about 90 GiB per arm and 640 GiB in a day, until the shared disk
was nearly full; a validator that replays every step needs that design changed,
not more disk.

## While the run is live

Keep the user-selected model and the frozen baseline protocol; substitute only
within existing authority and disclose any consequential change. Reuse the
framework's own logging, checkpoints and launcher; a custom loop is justified
only by the method or an unsupported operation, and then it is validated
against a trusted reference on a small case. Launch runs with disjoint device
allocations and keep the executable configuration, command,
device visibility and evaluator output with the run outputs. Before
interpreting any result, confirm that the intended path ran. For
policy-gradient health read `rl-training-health.md`; SFT
and offline DPO need their own objective-specific checks. Infrastructure work
ends when the experiment's decisive readiness check passes; no separate
infrastructure report is written.
