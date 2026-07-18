---
name: Kernel Engineering Review
description: Review production GPU-kernel changes for environment readiness, infrastructure reuse, numerical/API parity, benchmark integrity, architecture-bounded dispatch, and upstream-quality evidence.
category: kernel-engineering-review
priority: high
version: 1
created_at: 2026-07-18T00:00:00+00:00
---

# Kernel Engineering Review

Review artifacts and raw command output; do not trust the Engineer summary.

## Hard frontier-freshness gate

For the active stage, require `research/frontier/<stage>.json` to pass
`frontier_watch check --max-age-hours 6`. Independently inspect material cited
sources. Fail/continue when the snapshot is stale, offline, templated, lacks the
target-repository/toolchain/research-frontier surfaces, relies mainly on
secondary commentary, or does not state how findings affect the current plan.

Require an immediate refresh even inside the six-hour window after repeated
mechanism failures, before a substantial route change, and before an upstream
PR/final performance claim. `no_material_update=true` is valid only when real
queries and primary sources demonstrate that the current plan remains the best
supported choice.

## Hard environment gate

Fail/continue the environment stage when any of these holds:

- `ENVIRONMENT_AUDIT.json` is stale, generated from another project/Python, has
  no selected implementation capability, or reports a missing required
  capability.
- The chosen path needs TileLang but TileLang or a usable NVCC is absent; needs
  CUDA/CUTLASS but `nvcc`/`ptxas`/build tooling is absent; needs profiler or
  sanitizer evidence but those tools are unavailable without a documented
  alternative.
- Tests use one environment and benchmarks another without a compatibility and
  provenance argument.
- The Engineer ignored repository extras, lockfiles, CI, an existing backend,
  official harness, or maintained specialist/vendor implementation and instead
  wrote replacement infrastructure.
- `TOOLCHAIN_CANDIDATES.md` is absent, contains no category/platform registry
  queries, ignores a credible maintained package, or selects an archived/moved
  project without a pinned project-native justification.
- A compile/import/runtime failure was used to reject a kernel mechanism before
  distinguishing environment mismatch from implementation failure.

Installing everything is not readiness. Require the narrow project-compatible
stack and exact versions. Reject blind upgrades that invalidate the baseline.

## Correctness and integration

Require evidence appropriate to the public contract:

- reference parity for every output and gradient;
- numerical tolerance no weaker than the existing implementation;
- supported dtypes, shapes, ragged/varlen/options and layout behavior;
- repeated execution for races/nondeterminism when shared memory, atomics, or
  reductions are involved;
- unchanged public API and safe fallback when the new backend is unavailable or
  outside its validated hardware/shape domain;
- dependency remains optional unless the repository explicitly makes it core;
- no weakened tests, scorer, tolerance, synchronization, or workload.

## Performance evidence

Require same-machine, same-stack, isolated A/B measurement after warmup/JIT/
autotune. Report forward, backward, and combined paths when applicable; include
shape/dtype matrix, p50 and spread/quantiles, memory, and enough independent
runs to distinguish a win from noise. A single B200 supports a Blackwell-only
claim, not a universal GPU claim. Regressing shapes must fall back or be stated.

Do not reward a large speedup until the baseline agrees with the canonical
runner/reference and contention is excluded. Compile time must be excluded from
steady-state latency unless compile latency is the declared metric.

## Upstream readiness

The final diff should be narrow, documented, tested, and explainable by the
contributor. Require `RESULTS.md` to state:

- source revision and exact environment;
- selected/reused infrastructure and why;
- baseline and candidate commands;
- correctness and benchmark matrices;
- measured speedup with uncertainty;
- dispatch/fallback boundary;
- known limitations and negative results;
- overlap check against open upstream work.

Return `done` only when a maintainer can reproduce the result without guessing
which hidden package, compiler, profiler permission, or environment mutation
made it work.
