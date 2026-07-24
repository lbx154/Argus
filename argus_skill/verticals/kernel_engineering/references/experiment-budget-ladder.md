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
   CPU/other-kernel overhead. Use this timeline duration for the leverage gate.
   Multi-pass counter-profiler duration can be perturbed by replay and is not a
   wall-clock-share substitute.
4. **Leverage gate:** compare target-kernel time with end-to-end time using
   `kernel_engineering.leverage_gate`. Reject targets whose plausible gain cannot
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
   candidate that passed the micro A/B gate.

## Reviewer-controlled exploration budget

The normal bounded Engineer–Reviewer loop exposes three reviewed rounds; the
prompt's `Round: x/y` value is authoritative when configured differently. For a
correct, path-covered performance candidate:

1. Try 1 establishes a functional implementation and valid micro A/B.
2. Intermediate Tries diagnose prior results and change a material mechanism such as
   tiling, layout, fusion, launch structure, tensorization, or reuse.
3. The final Try applies the strongest remaining evidence-backed implementation.

The Reviewer, not a harness counter, judges whether each Try is materially
distinct and whether the direction is exhausted. A slower/noisy result before
the final available round must return `continue` with a concrete diagnosis and
next mechanism; do not certify optimize or advance to validate/report. On the
final available Try, retain a winner or return `replan_requested` with an evidence-backed
headroom/exhaustion judgment. Negative evidence remains valuable, but
validation/report are for a retained candidate, not ceremony around a failed one.

The next round receives the compact experiment card and checkpoint, not raw
profiler output. Raw reports remain on disk as evidence.
