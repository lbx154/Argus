---
name: "Experiment Plan Review"
description: "Review Build readiness before claim-bearing experimental execution."
---

# Experiment Plan Review

Use this in Build. Read the selected Idea in `HANDOFF.md`, actual entry points,
explicit configuration, real evaluator, strongest published baseline, and
positive control. The experiment programme may adapt later; do not require a
frozen global plan or a separate review file.

## Review

- The code implements the selected mechanism and intended information boundary.
- Baselines are real methods under fair data, compute, and evaluator access.
- The benchmark exercises the mechanism and has useful headroom.
- The positive control is known to be detectable through the same path.
- Metrics and uncertainty can distinguish the intended effect.
- Gold labels and scorer-derived fields are unavailable to the method.
- Available resources can run an informative comparison.

## RL training-configuration sanity

For RL post-training, report `rl_config_sanity` and auto-reject structurally
unlearnable settings:

- `num_generations` must permit useful within-group reward variation;
- reward extraction and matching must work on real outputs;
- `max_completion_length` must exceed the observed p95 completion need plus the
  rewarded closing token;
- Reference floors are a floor, not a target; use a limit as large as the context window and budget safely allow;
- optimizer, KL, sampling, batching, and checkpoint behavior must preserve a
  real learning signal.

RL post-training auto-fails when reward is constant, extraction never fires,
the response is truncated before reward, or effective gradients are absent.
Use `rl-training-collapse-diagnosis.md` for a concrete failure.

Return `ALIGNED`, `MISMATCH`, or `NOT_IMPLEMENTED` with the smallest direct
repair. Do not create another report.
