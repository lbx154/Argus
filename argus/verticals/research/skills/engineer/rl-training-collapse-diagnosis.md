---
name: "RL Training Health Diagnosis"
description: "诊断 PPO/GRPO/RLVR 训练的奖励、优势、梯度、KL、截断和吞吐异常。 Inspect policy-gradient run health before launch, during sustained anomalies and before attributing failure to a method. Standard offline DPO and SFT need their own objectives, not GRPO group-variance rules."
---

# RL Training Health Diagnosis

Use BEFORE launching a policy-gradient run, when live evidence suggests sustained
failure, or before deciding an underperforming method was fairly tested. Separate
observations, candidate causes and the action supported by the current evidence.
No single metric or universal hyperparameter threshold decides health. Knob and
telemetry names below are generic; map them to the chosen framework's equivalents.

## Before launch

- Inspect the actual algorithm, reward definition, advantage estimator and all loss
  terms. PPO, GRPO and RLVR are not interchangeable. Standard offline DPO compares
  preference pairs and does not require rollout groups or GRPO-normalized advantages.
- Make the run structurally learnable: verify the reward/extraction path on small
  known examples, including correct and incorrect outputs, and inspect tokenization.
- Derive `max_completion_length`, batch shape and `num_generations` from the actual
  task and estimator. Two unequal rewards can have nonzero sample variance;
  `num_generations >= 4` is neither necessary nor sufficient for nonzero variance.
  Larger groups trade sampling evidence against compute, not a universal health gate.
- Record distinct examples, sampling/repetition and train/evaluation separation.
  Reusing training examples is normal. Memorization requires evidence from coverage,
  held-out performance and behavior, not just a small count or a flat reward curve.
- Confirm a bounded pilot exercises the real training/evaluation path before the
  planned budget is spent. Preserve the configured hardware and comparison protocol.

## Interpret the actual learning signals

| Observation | What to inspect before acting |
| --- | --- |
| Per-group `reward_std` near zero / `frac_reward_zero_std` high | Verify aggregation and groups, reward extraction, difficulty, sampling and saturation. Buffer-wide averages can hide dead groups. |
| Zero normalized advantages | The corresponding policy/reward-gradient term may vanish. KL regularization, entropy, value or auxiliary losses can still update parameters. Inspect each term and measured gradients. |
| Policy loss near zero | A scalar policy loss can average to zero while its derivative is nonzero. Inspect `grad_norm`, parameter movement and objective terms; this is not a convergence or collapse test by itself. |
| KL or clip ratio growing | Check reference policy, update size, objective signs, reward scale and actual output quality. A trend warrants investigation, not an automatic invalid verdict. |
| Entropy or completion length changes | Compare expected task behavior, sampling settings, parse rate and held-out quality. Low entropy can reflect either useful certainty or harmful repetition. |
| Truncation / answer-parse failures | Inspect complete samples and gold normalization. Do not call a method bad when its outputs were cut off or its evaluator was miswired. |
| High training reward, weak held-out results | Test memorization, leakage or reward exploitation against independent examples and the frozen evaluation protocol. |
| OOM, non-finite updates, stalled steps/heartbeat | Check launcher status and error evidence; use the supervised stop/recovery path when confirmed. |

RL loss is not SFT loss. A rising or noisy policy loss alone does not establish
failure. Standard DPO/SFT diagnosis should use the relevant preference/supervised
loss, gradients, data quality and held-out behavior instead of group-relative rules.

## Live work and decisions

Use the run's existing logger, progress records and training curves when available.
Keep step-aligned reward, advantage/gradient, KL, length/parse, throughput and output
samples observable as applicable. Do not invent absent fields or a second trainer
just to satisfy a plot checklist. Distinguish warmup, brief anomalies and sustained
patterns relative to the configured schedule; include the evidence window.

For confirmed crashes, persistent invalid updates or demonstrated wasted work,
raise a `concern` through the supervisor with the observed condition, uncertainty
and smallest correction. Do not stop useful work on one noisy line. If required
telemetry is absent, request the smallest additional observation; do not label it
collapse. Preserve evidence when stopping and respect the remaining budget.

Use `misconfigured_run` only when the executed setup or evaluator is demonstrably
wrong; repair and re-run only when authorized and worthwhile. A fair comparison
can support `method_failure` within the tested scope, not a universal conclusion.
Use `infeasible_under_budget` when resource limits prevent the required experiment,
and state unknown when the available evidence cannot distinguish these cases.
