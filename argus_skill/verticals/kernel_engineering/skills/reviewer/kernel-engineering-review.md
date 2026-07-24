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

## Event-driven frontier-freshness gate

Require a fresh `research/frontier/<stage>.json` when scope is selected, a
relevant upstream/toolchain fact changes, repeated failures or a mechanism pivot
trigger re-search, or work reaches the PR/report boundary. Independently inspect
material cited sources. For a required refresh, fail/continue when the snapshot
is from another stage, offline, templated, lacks the target-repository/toolchain/
research-frontier surfaces, relies mainly on secondary commentary, or does not
state how findings affect the current plan.

Require a refresh after repeated mechanism failures, before a substantial route
change, and before an upstream PR/final performance claim.
`no_material_update=true` is valid only when real
queries and primary sources demonstrate that the current plan remains the best
supported choice.

Do not request another frontier refresh when the stage, route, environment, and
relevant public facts are unchanged.
Never ask the Engineer to read `FRONTIER_WATCH.jsonl` in full. Audit the current
snapshot and use `frontier_watch check` to verify the append-only ledger binding.

## Hard execution-versus-idea gate

Require every attempt to carry `OUTCOME.json` with separate `execution_status`,
`failure_class`, and `idea_status`. Reject any record that marks an idea
`refuted` because of a missing package, dependency conflict, compiler/toolchain
configuration, hardware access, profiler permission, benchmark infrastructure,
or invalid measurement. Those outcomes are `untested` or `inconclusive`.

Only a completed experiment in the intended audited environment may support or
refute an idea. A numerical or performance result can still be inconclusive if
the implementation may be wrong; use judgment and raw artifacts.

## IDGL / repeated-failure gate

The full suite is a certification gate, not a localization tool. After a red
gate, require the next round to run the first failing node/shape/configuration
with synchronous error reporting or sanitizer evidence. If the same failure
signature returns again without a new code/config diagnostic, set
`status="replan_requested"` and use `next_action` to ask L4 for a scoped repair task.
Do not certify repeated prose, refreshed timestamps, or another unchanged full
suite as forward progress.

If unmodified main is reproducibly red on the target hardware, do not demand an
impossible green baseline inside a no-edit mission. Preserve the red baseline,
end the mission via replan, and authorize a correctness-repair task limited to
the scoped kernel/backend/autotune surface. Return to baseline certification
after the repair.

## Hard environment gate

Fail/continue the environment stage when any of these holds:

- `ENVIRONMENT_AUDIT.json` comes from another project/Python, was not refreshed
  after an environment change, has no selected implementation capability, or
  reports a missing required capability.
- The selected project/backend dependency closure is red, the clean install
  cannot import the chosen path, or an advertised frontier result is used as the
  baseline even though its declared lockfile cannot reproduce it.
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

Require valid `attempts/<id>/LEVERAGE.json` before accepting a source edit. If
the selected kernel cannot plausibly clear the end-to-end noise floor, require a
failed candidate or higher-leverage target instead of rewarding a faster subkernel.
Require the target share to come from a low-overhead timeline aligned with the
end-to-end workload. Do not accept a multi-pass NCU counter-replay duration as a
wall-clock-share substitute; use focused NCU sections after the leverage gate to
diagnose a mechanism.

Require same-machine, same-stack, isolated A/B measurement after warmup/JIT/
autotune. Report forward, backward, and combined paths when applicable; include
shape/dtype matrix, p50 and spread/quantiles, memory, and enough independent
runs to distinguish a win from noise. A single B200 supports a Blackwell-only
claim, not a universal GPU claim. Regressing shapes must fall back or be stated.

Do not reward a large speedup until the baseline agrees with the canonical
runner/reference and contention is excluded. Compile time must be excluded from
steady-state latency unless compile latency is the declared metric.
Reject a performance conclusion when the benchmark matrix does not exercise the
changed dispatch/code path, or when a dirty candidate is labeled only by the
unchanged base commit without a diff hash/snapshot identity.

## Reviewer-controlled Try recall

The prompt states `Round: x/y` (normally three rounds). A correct, path-covered
candidate that is slower or within noise before the final round `y` is only a
candidate failure. Do not return `done`, certify optimize, or advance it to
validation/report. Return `continue`; require a compact regression diagnosis and
a materially distinct next implementation based on profile evidence, current
primary sources, and plausible headroom. Distinct means a changed mechanism—
tiling, layout, fusion, launch structure, tensorization, or reuse—not an unchanged
rerun or blind parameter sweep.

In the final available round, independently decide whether the direction has a
retained winner or is genuinely exhausted. Exhaustion requires concrete evidence:
the tested implementations, regression attribution, remaining plausible
mechanisms, and why none can reasonably clear the end-to-end noise/MDE. If
exhausted, return `replan_requested` with
`next_action` asking Planner to select a new mechanism;
do not mark a failed candidate `done` merely to send it through validate/report.

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
