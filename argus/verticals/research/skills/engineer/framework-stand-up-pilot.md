---
name: "Framework Stand-up Pilot"
description: "对入围的训练框架做限时的隔离安装与真实一步剖析,再决定是否引入独立 rollout 引擎。 Stand up each shortlisted framework in an isolated environment at a pinned SHA, run the nearest official example on the executed path, profile one real step, and decide engine use from measurements."
---

# Framework Stand-up Pilot

Use after `engineer/infrastructure-landscape-survey.md` produced a shortlist.
The pilot turns dated documentation claims into local measurements on the
allocated device. Nothing here changes the frozen model, data or comparison
protocol.

## Per candidate

1. Create an isolated environment per candidate (RL stacks carry incompatible
   pins; never install two candidates into one interpreter). Follow the global
   Project Environment and Dependencies skill for caches and interpreter policy.
2. Clone at a pinned SHA under `third_party/<name>/` and record the SHA, the
   release/tag it corresponds to, and the clone date.
3. Run the official example nearest the task, shrunk (fewer steps, smaller
   batch, shorter allowance) but on the executed path: the same trainer,
   objective, engine and launcher the full run would use.
4. Time box 45-60 minutes of wall time per candidate. Record install wall time,
   every pin conflict, and exact error text. A candidate that does not reach one
   valid update inside the box is recorded with the blocking error, not fixed
   indefinitely.
5. Launch through the durable runner so the logs are attributable:
   `python -m argus.tools.subagent submit ... --intent 'stand-up pilot: <candidate>@<sha>'`.

## One-real-step phase table

For the first real training step after warmup, record wall time per phase:
startup/compile, generation, environment/tool execution, log-prob scoring,
backward, weight sync, idle. Alongside record: rollout tokens/s and end-to-end
tokens/s, time to first valid update, device-level peak memory (count colocated
engine processes on the same device, not only the trainer), utilization,
truncation fraction, fraction of groups with reward contrast, and a
contended-device flag when another workload overlapped the measurement.

Branch on the dominant phase before deciding anything: a generation-dominated
step motivates a separate rollout engine; a backward- or sync-dominated step
does not, and an environment-dominated step asks for tool parallelism first.

## Engine on/off A/B

When an inference engine is a candidate, run the same shrunk example with the
engine on and off on identical prompts, seeds and device. Engine correctness is
rollout-vs-trainer log-prob agreement on the same sampled tokens: report the
importance-ratio distribution and the fraction outside the clip range. Identical
rewards are not the test; agreeing log-probs are.

## Output

For each candidate: GPU-hours per valid update and steps per day under the
allocated budget, derived from the measured step time. Record one row per
candidate in the project decision record, with the runner task id, run
directory and device. Choose the candidate, state what would change the
choice, and continue with `engineer/recipe-anchored-tuning.md`.
