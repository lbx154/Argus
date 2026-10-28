# Choosing ablations that distinguish explanations

Use this once the main method and the strongest fair baseline run correctly, or during review when the scientific review names a specific missing ablation.

Choose the smallest set of ablations that separates live explanations:

- remove or replace one claimed mechanism at a time;
- test a sensitive design choice only when it could explain the headline result;
- keep data, information, evaluator, and compute comparable;
- prioritize the run most likely to change the method or the paper's claim.

Implement each chosen ablation through the existing entry point and
configuration. Run a wiring check, then the claim-bearing comparison. Keep the
command, configuration, and raw output with the rest of the experiment. Do not
write a separate ablation plan, matrix, progress report, or experiment-log
document.

Interpret results against the mechanism, not against an expected direction.
Update the method or the next run when the evidence warrants it. The
experiments stay adaptive: they are done when the positive evidence is
persuasive, not when a predeclared list has been run.
