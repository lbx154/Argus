---
name: "Training Infrastructure Guide"
description: "Use maintained training or inference frameworks and size them to the experiment's real model, data, and hardware."
---

# Training Infrastructure Guide

Use this when the selected method needs model training or large-scale inference.
Reuse maintained infrastructure unless a custom loop is itself the contribution.

## Choose the framework

Inspect current official documentation and released code. Select the framework
that directly supports the method, model family, precision, and hardware:

- SFT or preference optimization: TRL, LLaMA-Factory, or another maintained
  project that implements the required algorithm;
- distributed RL or RLVR: veRL, OpenRLHF, or another maintained implementation;
- large-scale inference: vLLM or the model's official serving stack;
- diffusion or multimodal training: the maintained library used by the released
  baseline when practical.

Check the repository for deprecation, migration, or a named successor. Prefer the
current maintained path unless the older release has a capability the experiment
actually needs.

## Configure the real experiment

- Use a current, task-capable backbone unless the thesis specifically concerns an
  older or smaller model.
- Derive sequence length and generation limits from real examples; measure
  truncation rather than choosing a convenient round number.
- Reproduce the strongest published baseline under its documented protocol
  before comparing the new method.
- Keep candidate and baseline data, information, compute, and evaluator access
  comparable.
- Use the framework's standard logging, checkpointing, and distributed launch.
- Run a small wiring check before spending the full budget.

## Use allocated hardware

Declare accelerator demand through the project runner. For one distributed job,
use the framework's supported launcher; for independent conditions, submit
separate jobs with disjoint allocations. Measure observed throughput and memory
use during the run, then adjust batch size, sequence length, precision, and
parallelism when the allocation is materially idle.

For vLLM, set model length, generation length, concurrency, batched tokens, and
parallelism from the actual workload rather than conservative defaults. Feed
requests in batches instead of constructing a new engine per prompt.

Keep credentials in the capability vault or environment and downloaded weights
inside the project's model store. Do not print secrets or install project
dependencies into the Argus runtime environment.

Preserve the executable configuration, command, checkpoints when needed, and raw
metrics as direct run outputs. Do not create a separate infrastructure report or
duplicate experiment plan.
