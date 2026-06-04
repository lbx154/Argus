---
name: rl-training-collapse-diagnosis
description: "The authority on RL / preference post-training (PPO/GRPO/RLVR/DPO-style reasoning RL) run HEALTH and the hyperparameters that govern it: rollout reward variance, KL/clip, num_generations, max_completion_length / rollout length, learning rate, steps/horizon, truncation & answer-parse rates. Use it THREE times: (1) BEFORE launching an RL run, to set those knobs to non-degenerate values so the run is learnable; (2) WHILE watching live training logs (progress.jsonl / trainer stdout), to judge continue vs raise-concern; (3) WHEN a method underperforms / no-go, to attribute the cause — `misconfigured_run` (re-run, don't kill the idea) vs `method_failure` vs `infeasible_under_budget` — BEFORE retiring the idea. NOT for plain SFT/supervised-loss debugging, and NOT for offline benchmark/eval scoring unrelated to training-run health."
category: rl-posttraining-run-diagnosis
version: "1.1"
scientist_model: gpt-5.5
created_at: "2026-06-02T00:00:00+00:00"
---

# RL Training Collapse Diagnosis

This skill is the authority on whether an RL / preference post-training run is a
**fair, learnable run** — and on the hyperparameters that decide that. Collapse
means the run can no longer learn anything useful, so every additional step
burns GPU for nothing and the resulting checkpoint is not valid evidence. This
skill is the *criteria*; the call is still yours. Judge the signals, do not
pattern-match a single noisy log line.

## When to use (config-time, monitoring-time, AND verdict-time)

- **Before launching an RL run (config-time):** read the collapse signatures
  below and set the knobs so the run is *structurally learnable* — most
  importantly a `max_completion_length` / rollout length large enough that the
  reasoning finishes (low truncation under the intended template),
  `num_generations` >= 4 so per-group reward variance can be non-zero, an
  RL-scale learning rate, and enough steps to show movement. **Equally
  decisive, and the most-forgotten knob: the training-set itself must be large
  and diverse enough that the policy cannot simply memorise it.** Admitting
  only a handful of distinct task ids — especially with curriculum repeats over
  the same ids — guarantees the policy reaches every answer within a few steps,
  reward pins at the ceiling, per-group advantage goes to zero, and the rest of
  the run is zero-gradient busywork that *looks* like a strong reward curve.
  Treat the count of admitted/distinct tasks as a first-class learnability
  precondition: it must be large relative to `num_generations` x batch x steps
  so the model keeps meeting *unseen* problems, not the same few on repeat. A
  small pure slice that satisfies a curriculum/purity filter is NOT a substitute
  for diversity — a clean 10-problem set is a memorisation trap, not a fair run.
  A run that is doomed by its config (or its tiny dataset) wastes the whole GPU
  budget and then masquerades as a dead idea.
- **While the run is live (monitoring-time):** map the streaming
  `progress.jsonl` / trainer stdout onto the signatures and decide continue vs
  raise-concern.
- **When judging an underperformance / no-go (verdict-time):** before retiring
  or pivoting away from the METHOD, confirm the executed run was fair using the
  same signatures; classify `misconfigured_run` / `method_failure` /
  `infeasible_under_budget`. A `misconfigured_run` is re-run with the named
  correction — the idea is NOT recorded dead. Only after one fair run still
  loses is `method_failure` justified.

## When NOT to use

Plain SFT / supervised fine-tuning loss debugging, generic offline benchmark or
eval scoring that is not about a training run's health, or non-RL methods.

The cardinal rule of RL monitoring: **RL loss is not SFT loss.** A noisy or even
rising policy loss is normal and is NOT a failure. Never stop an RL run just
because "loss went up". Judge by the *learning signal* (reward variance,
gradient, KL, entropy, output quality), not by loss magnitude.

## What "collapse" actually is

In policy-gradient RL (and especially group-relative methods like GRPO/RLVR),
learning requires a **non-degenerate advantage signal**. If every sample in a
group earns the same reward, the advantage is zero, the gradient is zero, and
the policy stops moving. So the deepest collapse signature is not "bad numbers"
— it is **the learning signal going to zero or going degenerate**. Map what you
see to one of the families below.

## The harness pre-computes these signals for you (advisory)

You do not have to scrape the raw logs by hand. At the `run` and `analysis`
stages the harness runs an **advisory** gate, `rl_training_health`, that reads
each live/completed optimizer run's own `verl_metrics.jsonl` /
`progress.jsonl` / `reward_trace.jsonl` and prints the collapse-relevant
numbers over the tail window into your review context: advantage span,
grad-norm, reward ceiling/floor hits, entropy trend, and training-set
diversity (unique `task_id` count vs rollout rows). It emits neutral signal
tokens — `zero_advantage`, `near_zero_grad_norm`, `reward_ceiling_saturation`,
`reward_floor_stuck`, `entropy_declining`, `low_task_diversity`,
`variance_metric_masks_saturation`, `kl_blowup_candidate`, `nan_or_inf_metric`
— that map onto the signatures below.

That gate is a **fact extractor, not the authority**: it never blocks and
never rules. The authority is this skill plus your judgment. Read its numbers,
apply the transient-vs-sustained test below, and decide continue vs concern. A
`low_task_diversity` + `reward_ceiling_saturation` pair, for instance, is the
fingerprint of memorising a tiny admitted-id set — confirm it against the
`unique_task_ids` line before you trust a high reward.

## Mandatory: emit a live training-curve plot (every RL run)

**Hard requirement — every RL optimizer-step run MUST produce a training-curve
plot so the run is visually monitorable; a completed run with no curve is not
citable evidence.** The harness enforces this: `rl_training_plots` is advisory
at the `run` stage and **structural (blocking) at the `analysis` stage**, so an
unplotted completed optimizer run cannot be carried into analysis.

Contract the runner must satisfy:

- Write/refresh a curve image into **`<run_dir>/plots/training_curve.png`**
  (`.pdf`/`.svg` also accepted; the filename must contain a curve token such
  as `training_curve` / `optimizer_metrics` / `reward_curve`).
- Plot the collapse-relevant series **vs optimizer step**, sourced from the
  run's own `progress.jsonl` / `verl_metrics.jsonl`: at minimum
  `reward_mean` (+ `reward_std`/`frac_reward_zero_std`), `pg_loss`,
  `grad_norm`, `kl`, `entropy`, and `throughput`.
- **Refresh it live** during training (e.g. every N optimizer steps from the
  monitoring loop) and write a final version at completion — do not defer all
  plotting to the analysis stage. Live curves are how you (and the operator)
  judge continue-vs-raise-concern in real time.
- Keep it a thin wrapper over the framework's own logger output; do not
  hand-roll a training loop to produce it.

This plot is infrastructure, not a paper figure — it lives under the run dir
and is part of run-stage evidence. Paper-facing figures are still produced
separately by the results-analysis-and-figures skill.

## Collapse signatures

Read these off `progress.jsonl` / trainer stdout. Field names vary by trainer;
match on meaning, not exact keys.

1. **Reward-variance death (the most important, and the most missed).**
   - `reward_std → 0`, `frac_reward_zero_std → 1` (or per-group reward std all 0).
   - In GRPO/RLVR this means *zero advantage* ⇒ zero gradient ⇒ no learning.
   - Two distinct causes, same fatal symptom:
     - **all-wrong collapse**: reward pinned at the floor (e.g. correctness
       reward stuck at 0). Task too hard, or the reward/answer extraction is
       broken so *correct* completions still score 0.
     - **all-right / saturated collapse**: reward pinned at the ceiling. Task
       too easy, reward-hacked, **or the admitted training set is so small the
       policy has memorised every problem** (a handful of distinct task ids,
       or curriculum repeats over the same ids); nothing left to learn. Check
       the distinct-task count, not just the reward level — a high flat reward
       on 10 memorised problems is collapse, not success.
   - A correctness/verifier reward whose mean is stuck at 0 for the whole tail
     window while completions *look* plausible is a screaming sign the reward
     extractor (boxed-answer parse, answer normalization, gold matching) is
     broken — not that the model is hopeless.
   - **`frac_reward_zero_std` reads differently depending on how it is
     aggregated — do not trust a low value as an all-clear.** Computed
     per-group on one batch (e.g. at curriculum screening) it honestly reads
     `1.0` when every group is saturated. But the *same* saturation can read
     `~0.0` when the metric is averaged over a sliding rollout buffer that
     mixes many steps/groups: a handful of still-varying groups dilute the
     fraction toward zero even though the policy has already memorised the set.
     So `frac_reward_zero_std ≈ 0` during training is **not** proof of healthy
     variance. Cross-check with the advantage span, the per-group/per-family
     reward std on the latest batch, and the distinct-task count before you
     call variance "healthy" — that buffer-diluted `0.0` is exactly how a
     memorised tiny-set run gets mislabelled healthy.

2. **Gradient / update death.**
   - `grad_norm → 0` and `loss → exactly 0` sustained.
   - Exactly-zero policy loss is NOT convergence in RL — it almost always means
     advantages are all zero (see #1), i.e. the optimizer has nothing to push on.

3. **KL blow-up / policy divergence.**
   - KL to the reference policy climbing without bound, often with reward
     briefly spiking then outputs turning to gibberish. The policy has walked
     off the manifold; results are invalid even if a transient reward looks good.

4. **Entropy pathology.**
   - `entropy → 0`: mode collapse / deterministic repetition (one canned output).
   - `entropy` pinned high with **no reward gain** over a long stretch: the
     policy is flailing, not exploring productively.

5. **Completion-length collapse.**
   - `mean_completion_length → 0` (empty/degenerate outputs), or
   - length **pinned at the cap** with high `clipped_ratio` (e.g. ≳ 0.25):
     completions are truncated, so boxed answers / final answers are cut off and
     the comparison is between truncated junk. This invalidates the reward.

6. **Format / parse collapse.**
   - `format_reward` decaying and/or the parseable-answer rate (boxed-answer
     extraction) trending to 0: the model stopped emitting the structure the
     verifier needs, so correctness reward can never fire.

7. **Reward hacking (reward UP, but fake).**
   - Reward rises while outputs degenerate: length exploits, repetition, copying
     the prompt, emitting the answer format without real reasoning, KL drifting.
   - Do not be fooled by a rising reward curve alone — sanity-check the actual
     completions in the log tail.

8. **Throughput stall.**
   - No new optimizer steps / frozen heartbeat / step_time exploding / OOM or
     traceback in stderr. Distinct from collapse but equally stop-worthy.

## The transient-vs-sustained judgment (this is where your judgment matters)

A single bad log line is not collapse. The same number means different things
depending on *when* and *for how long*:

- **Early / warmup zeros are usually fine.** At the very start, all completions
  may be wrong (reward floor) or the trainer may be ramping; reward_std can be 0
  for a few steps and then recover as the policy finds a gradient. Do not stop on
  step-1 zeros.
- **Tail-window persistence is collapse.** If the *last several* logged steps
  (a tail window, not one point) all show zero reward variance + zero gradient +
  zero correctness reward, the run has flatlined and will not recover on its own.
  This is the case that must be stopped — early "health passed" markers computed
  from only the first 2–3 logs are exactly how a dead run sneaks through.
- **Recoverable dip vs trend.** A one-step spike in KL or a momentary length dip
  that recovers is degrading, not collapsed. A monotone trend over the tail
  window is collapse.
- **Direction of the reward signal matters more than its level.** Low-but-rising
  reward with healthy variance = keep going. Higher-but-flat reward with zero
  variance = dead.

Concrete worked example (the failure this skill exists to catch): an RLVR run
emits a healthy first two logs (nonzero reward std), an early health gate marks
it "passed", then the final ~10 steps all read `reward_std=0`,
`frac_reward_zero_std=1`, `grad_norm=0`, `loss=0`, and correctness/boxed reward
mean `=0`, while format reward is a small constant. Verdict: **collapsed**
(reward-variance death + gradient death + parse/answer-extraction failure). The
early marker is irrelevant; the tail window is what counts. Stop it.

## Mapping a verdict to your decision

Stopping is expensive (it halts the run and opens a discussion with the
engineer), so only stop on a *genuine, sustained* collapse — but do stop, do not
let a flatlined run burn to completion.

- **Sustained collapse signature across the tail window** → raise a `concern`
  (this halts the run). In the concern, name (a) which signature fired, (b) the
  most likely upstream cause, and (c) what the engineer should re-check before
  relaunching. Useful causes to point at, by signature:
  - reward-variance death / correctness reward stuck at 0 → prompt format,
    answer normalization, boxed/answer **extraction and gold-matching**, reward
    function wiring, task difficulty / curriculum, **training-set size /
    distinct-task diversity (memorisation on a tiny admitted set)**.
  - KL blow-up → KL coefficient / target, learning rate, clipping range.
  - entropy collapse → entropy bonus, temperature/top-p, lr too high.
  - length pinned at cap / high clip ratio → max_completion_length, truncation,
    length penalty.
  - reward hacking → reward shaping, length/ format reward weighting, add a
    verifier check.
- **Transient / early / recoverable, or low-but-rising with real variance** →
  `continue`, leave `concern` empty, and mark health `degrading` or `stuck` so
  the next check tightens the interval and watches the tail window.
- **Crash / OOM / NaN / frozen throughput** → stop regardless of reward shape.

Do not stop a run that is merely imperfect (slightly noisy, a cosmetic warning,
RL loss wiggling). Reserve the halt for real collapse or wasted spend.
