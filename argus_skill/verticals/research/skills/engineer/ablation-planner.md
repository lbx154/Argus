---
name: "Ablation Planner"
description: "Choose and run only the ablations needed to explain the selected method's result."
---

# Ablation Planner

Use this in Experiment after the main method and strongest fair baseline run
correctly. Use it in Review only when the scientific review identifies a
specific missing ablation.

Choose the smallest set of ablations that separates live explanations:

- remove or replace one claimed mechanism at a time;
- test a sensitive design choice only when it could explain the headline result;
- keep data, information, evaluator, and compute comparable;
- prioritize the run most likely to change the method or paper claim.

Implement each chosen ablation through the existing entry point and configuration.
Run a wiring check, then the claim-bearing comparison. Preserve the command,
configuration, and raw output as normal experiment work products. Do not create
a separate ablation plan, matrix, progress report, or experiment-log document.

Interpret results against the mechanism, not against an expected direction.
Update the method or next run when evidence warrants it. Experiment remains
adaptive; completion is based on a persuasive positive evidence package, not on
executing a predeclared list.
