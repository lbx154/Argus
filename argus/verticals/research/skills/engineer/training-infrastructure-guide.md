---
name: "Training Infrastructure Guide"
description: "为已确定的机器学习实验选择训练/推理框架的入口:按程序调查、试点、调参,不依赖记忆中的框架名。 Entry point for choosing and validating infrastructure for an actual ML training or large-inference workload: survey live sources, stand up pinned candidates, tune from the official recipe."
---

# Training Infrastructure Guide

Use only when the selected method requires training or substantial inference.
Follow the global Project Environment and Dependencies skill for interpreter,
installation and cache policy; do not initialize an ML stack for unrelated work.

No framework, engine or model name in this skill or in memory is a
recommendation. The current answer is produced at project time from live
sources and local measurement, and stored as a dated project Skill
(`engineer/<task-class>-infrastructure-decision.md`) that later projects
re-verify. If such a record exists for this task class and hardware and today is
inside its horizon, start from it; otherwise run the procedure:

1. `engineer/infrastructure-landscape-survey.md`: fetch live sources through the
   source cache, run successor discovery on every remembered name, fill one
   evidence card per candidate, shortlist 2-3 with exclusion reasons.
2. `engineer/framework-stand-up-pilot.md`: isolated environment per candidate,
   clone at a pinned SHA under `third_party/<name>/`, run the nearest official
   example on the executed path, profile one real step, decide engine use from
   the dominant phase and the engine on/off log-prob agreement.
3. `engineer/recipe-anchored-tuning.md`: copy the official example config at the
   pin with provenance, derive task-bound knobs from a dev pool, one factor per
   supervised pilot, escalation note before any full run.

Throughout: keep the user-selected/frozen model and baseline protocol; substitute
only within existing authority and disclose consequential changes. Reuse
supported infrastructure when it fits; a custom loop is justified only by the
method or an unsupported operation and is validated against a trusted reference
on a small case. Use the framework's own logging, checkpoints and launcher, submit
through the durable runner with disjoint allocations, and preserve executable
configuration, command, device visibility and evaluator output in the run outputs.

Before interpreting a result, confirm that the intended path ran. For
policy-gradient health use `engineer/rl-training-collapse-diagnosis.md`; SFT and
offline DPO need objective-specific checks. Stop infrastructure work once the
experiment's decisive readiness check passes; no separate infrastructure report is
required.
