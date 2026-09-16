---
name: "Infrastructure Choice Review"
description: "只读审阅训练/大规模推理实验的基础设施决策记录:来源是否实时抓取、候选是否钉住、试点数字是否可追溯。 Read the dated infrastructure decision record behind a training or substantial-inference experiment and judge whether its sources, pins and pilot numbers are traceable."
---

# Infrastructure Choice Review

Applies only when the experiment trains a model or runs substantial inference.
Do not apply it to analysis, evaluation-only or data-preparation experiments.
Read only; the Reviewer changes nothing and needs no tools beyond reading files.

## What to check

1. A dated decision record exists at
   `engineer/<task-class>-infrastructure-decision.md` in the project skill
   directory, and the description states the survey date, task class and
   hardware with a re-verify date.
2. Sources are cached: every cited URL has an access date and a path under
   `.argus/sources/` that exists.
3. Candidate release and last-commit dates were fetched (a cached path backs
   each date), not asserted from memory; rejected candidates carry an exclusion
   reason that points at a card field.
4. The chosen candidate is pinned: a SHA under `third_party/<name>/` and the
   release it corresponds to.
5. Pilot numbers trace to durable-runner logs (task id, run directory) on the
   allocated device, including the engine on/off A/B and the rollout-vs-trainer
   log-prob agreement measurement when an inference engine is used.
6. The official example at the pin is the configuration baseline and the method
   is a readable diff against it, with a provenance comment.
7. Today is inside the record's horizon for this task class and hardware; if the
   task class or hardware changed, the record no longer applies.

## Hold reasons

Hold when a date or pilot number has no cached source or runner log behind it,
when the pin is missing, when the engine was adopted without a log-prob
agreement measurement, or when the record is outside its horizon and was not
re-verified. State the missing artifact by name.

## Explicit non-holds

- Never demand a particular framework, engine or model; judge the procedure.
- Never hold because a newer release exists if the survey saw and judged it.
- Never apply this skill to non-training experiments.
- An offline survey recorded as unverified with its failed URLs is a documented
  limitation: note it in the review and let work continue.
