---
name: rl
description: "Post-training language models with reinforcement learning (RLVR, GRPO and its kin): a fast current stack, the numbers that tell a run is healthy, and what this team's runs have learned"
---

This skill grows with the team's runs. When an RL run teaches something a later one can use, add
it here (copy this folder to the skills home under the same name): as a script or a check where
you can, with the run it came from and the numbers that showed it.

## A strong start

- The stack: an RL framework whose rollouts run on an inference engine, such as verl, OpenRLHF or
  TRL's GRPO trainer with vLLM. Generation is most of an RL run's time, and the engine is the
  difference between hours and days. Check each framework's current release and examples for the
  algorithm you need before choosing.
- The model: the newest open family that fits the GPUs (check the releases now), in the variant
  the recipe you follow used, base or instruct. On two 48 GB GPUs that means full training of a
  model of a few billion parameters, or LoRA on a larger one.
- The recipe: start from a published recipe on the same model family: its group size, KL and
  clipping, learning rate and response length. Change one thing at a time from there.
- The reward: a verifiable task with a reward function that is checked on known right and known
  wrong answers before any training.

## Is the run healthy

`node <this folder>/rollouts.mjs <file.jsonl>` reads a step's rollouts, one JSON object a line
with `response` (or `completion`), `reward`, and if it has them `finish_reason` (or `truncated`)
and `group` (the prompt's id). It prints what to watch:

- the share of responses cut off at the length cap: above a few percent, raise the cap, since a
  cut-off response scores zero and teaches the model to stop early;
- the mean reward, and the share of groups whose rewards all tie, which carry no learning signal
  (all right: too easy; all wrong: too hard);
- the mean response length, whose trend over steps shows collapse or runaway.

Evaluate the untrained checkpoint under the identical protocol first: every gain is measured from
it. With an engine in the loop, the engine's and the trainer's log-probabilities of the same
tokens should agree; identical rewards do not show it.
