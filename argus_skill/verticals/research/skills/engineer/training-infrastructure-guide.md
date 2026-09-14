---
name: "Training Infrastructure Guide"
description: "为已确定的机器学习实验选择训练/推理框架。 Choose and validate infrastructure for an actual ML training or large-inference workload, with comparable baselines, allocated hardware and reproducible configuration."
---

# Training Infrastructure Guide

Use only when the selected method requires training or substantial inference.
Follow the global Project Environment and Dependencies skill for interpreter,
installation and cache policy; do not initialize an ML stack for unrelated work.

1. Inspect the method, model family, data, precision and allocated hardware. Read
   the maintained framework's official documentation for the required capability.
   TRL, LLaMA-Factory, veRL, OpenRLHF and vLLM are examples, not required choices.
2. Reuse supported infrastructure when it fits. A custom loop is justified when
   needed by the method or unsupported operation; validate it against a trusted
   reference on a small case. Its being custom does not itself invalidate results.
3. Keep the user-selected/frozen model and baseline protocol. Substitute only
   within existing authority and disclose consequential changes. Match candidate
   and control data, initialization, compute and evaluator access as required.
4. Size batch, sequence/generation limits and parallelism using actual examples,
   truncation, memory and throughput from a bounded pilot. Do not require maximal
   GPU occupancy if that would violate the experiment or overspend its budget.
5. Use the framework's supported logging, checkpoints and launcher. Submit
   independent experiments through the existing scheduler with disjoint allocations.
6. Preserve executable configuration, command, actual device visibility, evaluator
   output and necessary checkpoints in the normal run outputs. Honor configured
   model/data caches and keep credentials out of artifacts.

Before interpreting a result, confirm that the intended path ran. For policy-gradient
health use `engineer/rl-training-collapse-diagnosis.md`; SFT and offline DPO need
objective-specific checks. Stop infrastructure work once the experiment's decisive
readiness check passes; no separate infrastructure report is required.
