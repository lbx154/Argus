# Kernel Experiment Budget Ladder

Use the cheapest rung that can change the decision. Never jump directly from an
idea to a full correctness suite or an all-section profiler run.

1. **Route proof:** one representative invocation with dispatch logging. Stop if
   the intended backend/kernel is not exercised.
2. **Stable microbaseline:** warm JIT/autotune, then record end-to-end median and
   spread for one path-aligned shape.
3. **Timeline profile:** use the project-native profiler or torch profiler to
   identify kernel share, launch count, and CPU/other-kernel overhead.
4. **Leverage gate:** compare target-kernel time with end-to-end time using
   `kernel_engineering.leverage_gate`. Reject targets whose plausible gain cannot
   clear the required total speedup/noise floor.
5. **Focused NCU/NSYS:** collect only the launches and sections needed to choose
   one mechanism. Avoid all-section replay unless the decision requires it.
6. **One source change:** preserve baseline/candidate identity and diff hash.
7. **Targeted correctness + micro A/B:** stop and record no-go if the result does
   not clear noise. Do not run the full suite.
8. **Certification:** full correctness and benchmark matrix only for a retained
   candidate that passed the micro A/B gate.

The next round receives the compact experiment card and checkpoint, not raw
profiler output. Raw reports remain on disk as evidence.
