# Kernel Experiment Budget Ladder

Use the cheapest rung that can change the decision. Never jump directly from an
idea to a full correctness suite or an all-section profiler run.

1. **Route proof:** one representative invocation with dispatch logging. Stop if
   the intended backend/kernel is not exercised.
2. **Stable microbaseline:** warm JIT/autotune, then record end-to-end median and
   spread for one path-aligned shape. Report environment/bootstrap, compile, and
   cache-fill time separately; do not mix them into steady-state latency unless
   compilation is the declared metric.
3. **Timeline profile:** use the project-native profiler, torch profiler, or a
   low-overhead system trace to identify kernel share, launch count, and
   CPU/other-kernel overhead. Use this timeline duration for the leverage analysis.
   Multi-pass counter-profiler duration can be perturbed by replay and is not a
   wall-clock-share substitute.
4. **Leverage analysis:** compare target-kernel time with end-to-end time using
   `kernel_engineering.leverage_gate`. Set aside targets whose plausible gain cannot
   clear the required total speedup/noise floor.
5. **Focused NCU/NSYS:** only after leverage passes, collect the launches and
   sections needed to choose one mechanism. Avoid all-section replay unless the
   decision requires it.
6. **One source change:** preserve baseline/candidate identity and diff hash.
7. **Targeted correctness + micro A/B:** a correct candidate that does not clear
   noise is a failed candidate, not immediate proof that the direction is exhausted.
   Use the remaining reviewed Try budget for a materially distinct implementation
   informed by profiling, current primary sources, and the measured regression.
   Do not run the full suite.
8. **Certification:** full correctness and benchmark matrix only for a retained
   candidate that cleared the micro A/B comparison.

## Reviewer-controlled exploration budget

The budget is evidence-based, not a fixed number of rounds. `Round: x/y` is an
emergency runtime boundary and must not be interpreted as the number of
candidate Tries. A Try exists only after a candidate is correct, path-covered,
and validly measured. Environment, dependency, command, toolchain, benchmark,
or measurement-infrastructure repair rounds consume no Try.

For each valid Try, the Reviewer judges whether it is materially distinct and
whether measured headroom supports another mechanism. Distinct means tiling,
layout, fusion, launch structure, tensorization, reuse, or another physical
change—not an unchanged rerun or cosmetic knob sweep. Continue the same mission
while evidence is progressing. Retain a winner, or return `replan_requested`
when concrete evidence shows the direction cannot clear the end-to-end
noise/MDE. Negative evidence remains valuable, but certification and reporting
are for a retained candidate, not ceremony around a failed one.

The next round receives the compact experiment card and checkpoint, not raw
profiler output. Raw reports remain on disk as evidence.
