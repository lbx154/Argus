---
name: rl
description: "Post-training language models with reinforcement learning (RLVR, GRPO and its kin): a fast current stack, the numbers that tell a run is healthy, and the team's ledger of past runs"
---

## The ledger

Every RL run the team makes adds one line to `rl/runs.jsonl` in the team's skills folder (Where
things are names it; make the folder if it is missing): the model, the framework and its version,
the GPUs, the hours, the throughput, the evaluation before and after training with its benchmark
and protocol, and the path to the run. A new run starts from the closest line that worked, and
says in its brief why it departs from it. When a run, or the operator, teaches something the ledger
cannot hold, write it into the team's `rl` skill beside this one, as a check where you can.

## A strong start

- The models, data, evaluation and framework: those of the closest recent work on the question,
  so that its baselines run from their official code, checked now. The model is a base checkpoint
  (Qwen3 or Qwen3.5, 4B or 8B Base), not a post-trained release, which has had its RL already and
  thinks for tens of thousands of tokens. For math reasoning with no closer work, as of October
  2026: DAPO-Math-17k with a verl or TRL recipe, evaluated on AIME 2024 to 2026 (16 to 32 samples
  per problem, averaged) and MATH-500.
- The stack: an RL framework whose rollouts run on an inference engine, such as verl or TRL's GRPO
  trainer, used as it ships: its loss, masking, importance correction and weight sync are tested,
  so a method changes only the part it is about, and a model the framework trips on gets a patch
  in that one place, not a trainer of your own. Generation is most of an RL run's time: in TRL set
  `use_vllm=True` (it is off by default), and leave the engine's speed settings at their defaults
  (CUDA graphs on, a whole batch of prompts in one call). Check each framework's current release
  and example for the algorithm you use.
- Memory: full training with Adam needs about 16 bytes per parameter before activations and the
  engine's share, LoRA about 2. Size the model to the GPUs this run has. In RL, LoRA on every
  linear layer, the MLP's included, matches full training even at a small rank; LoRA on attention
  alone falls short.
- The recipe: start from a published recipe on the same model family (group size, KL and clipping,
  learning rate, response length) and change one thing at a time.
- The reward: a verifiable task, with the reward function checked on known right and known wrong
  answers before any training.
- The data: sample the starting model 8 times on a few hundred training prompts and run
  `node <this folder>/groups.mjs rollouts.jsonl`: only the prompts it sometimes solves teach it.

## Is the run healthy

The frameworks log what to watch; read their names in the version you run:

- the share of responses cut off at the length cap (TRL `completions/clipped_ratio`, verl
  `response_length/clip_ratio`): above a few percent, raise the cap, since a cut-off response
  scores zero and teaches the model to stop early;
- the share of groups whose rewards all tie (TRL `frac_reward_zero_std`), which carry no learning
  signal: above about half, the data is too easy or too hard for this model, and harder data, or
  data filtered by the starting model's pass rate, comes before any other change;
- the gap between the engine's and the trainer's log-probabilities of the same tokens (TRL
  `sampling/sampling_logp_difference`): in bf16 it is never zero, and about as large as the
  trainer's own gap between two of its code paths. The frameworks correct for it by default (TRL
  `vllm_importance_sampling_correction`), so read it in the run's log, with the share of samples
  the correction discards, rather than gating training on it; look into it when it grows.

Evaluate the untrained checkpoint under the identical protocol first: every gain is measured from
it. Two traps make gains that are not the method's: on Qwen models, RL with random rewards has
raised MATH-500 by double digits (contamination), so confirm a gain on a second model family or a
held-out benchmark; and a 30-problem benchmark such as AIME moves by 5 to 15 points between seeds,
so report several seeds.
