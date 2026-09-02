---
name: "Research Experiment Runner"
description: "Run adaptive, reproducible research experiments until the selected method clears the Paper entry bar."
---

# Research Experiment Runner

Read project-root `HANDOFF.md`, the code, explicit run configuration, and the
direct outputs relevant to the current comparison. Do not recursively load old
research reports.

## Adaptive programme

- Every executed run must be reproducible from its code, configuration, command,
  environment requirements, and raw output.
- Methods, baselines, benchmarks, controls, scale, and next experiments may
  change when development evidence justifies the change.
- Preserve earlier raw outputs; do not require a frozen global experiment plan.
- Use official evaluators and the strongest fair same-information baseline when
  available.
- Reproduce real strong published baselines; a renamed local heuristic is not a
  published baseline. Use current models and appropriate public or official
  benchmarks when the claim depends on them.
- Run a known detectable positive control through the same executed path before
  interpreting a null or negative outcome. If it fails, diagnose the harness,
  evaluator, truncation, scale, or information boundary first.
- Separate scientific failure from broken implementation, evaluator, controls,
  information leakage, or inadequate scale.

Treat a weak or mixed result as method-development evidence. Repair the largest
claim-relevant defect and run the next decisive comparison. Do not turn an
unfinished implementation into a negative-result paper, reopen idea selection,
or request rollback.

## Paper entry

Continue Experiment unless all three conditions hold:

1. mechanism-relevant wins clearly exceed losses;
2. headline and primary comparisons win;
3. the strongest same-information baseline is beaten.

When they hold, overwrite `HANDOFF.md` with the thesis, winning comparisons,
essential losses or limitations, strongest baseline, figures/data to use, and
the minimum reproducibility pointers Paper needs. Code, configurations, raw
outputs, and figures remain direct work products rather than handoff files.
Replace the file completely, start it with `# HANDOFF — EXPERIMENT`, and do not
append history or add another schema.
