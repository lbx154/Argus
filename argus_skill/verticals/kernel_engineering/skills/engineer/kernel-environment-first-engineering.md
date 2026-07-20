---
name: Kernel Environment-First Engineering
description: Execute production GPU-kernel optimization only after proving the project-native toolchain, mature infrastructure, correctness oracle, and benchmark environment are installed and compatible; distinguish environment failures from kernel failures and avoid rebuilding existing frameworks.
category: kernel-engineering
priority: high
version: 1
created_at: 2026-07-18T00:00:00+00:00
---

# Kernel Environment-First Engineering

## Non-negotiable principle

The environment is part of the implementation. A missing compiler, architecture
target, package extra, profiler, benchmark service, or version-compatible DSL can
make correct code fail or make slow code look fast. Do not treat that as an
algorithm verdict.

Do not rebuild infrastructure that professionals already use. Before writing a
kernel, inspect the repository and current primary sources for its canonical
stack. The analogue of writing an RL trainer while ignoring veRL is writing a
TileLang/CUDA kernel while ignoring the project's TileLang extra, backend
registry, reference kernels, benchmark runner, CUTLASS/CuTe path, or vendor
library.

## Continuous online frontier loop

Online research is event-driven, not repeated mechanically at every stage. Search
when selecting scope, after relevant upstream/toolchain changes or repeated
mechanism failures, before changing direction, and immediately before a PR or
final report:

1. Search the target repository's latest main, releases, open/merged PRs,
   issues, benchmark changes, and maintainer discussion.
2. Search current official release notes/docs for the selected GPU, compiler,
   DSL, profiler, and specialist packages.
3. Search recent papers/preprints and author repositories for the exact op,
   adjacent mechanisms, hardware, and benchmark.
4. Search current adjacent implementations and stronger public baselines.
5. Record focused queries, primary sources, findings, and decision impact. A
   real search that finds no material update is acceptable; skipping it is not.

Use:

```bash
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.kernel_engineering.frontier_watch template \
  --stage <stage> > /tmp/frontier.json
# Replace every placeholder using real online sources.
"${ARGUS_SKILL_PYTHON:-python}" -m \
  argus_skill.verticals.kernel_engineering.frontier_watch record \
  --project-root . --stage <stage> --input /tmp/frontier.json
```

Read the full protocol at
`argus_skill/verticals/kernel_engineering/references/frontier-search-protocol.md`.
If the network is unavailable, record the blocker and continue only local work
that does not claim current-frontier completeness; the stage cannot pass.
Reuse the recorded frontier snapshot while the target, mechanism, toolchain, and
relevant public facts remain unchanged. A stage transition alone is not a refresh
trigger. `FRONTIER_WATCH.jsonl` is append-only audit output: never load it in full.
Use the current snapshot plus `frontier_watch check`, which verifies its ledger
binding without injecting the ledger into model context.

## IDGL: Idea–Diagnosis–Gate Loop

Follow `references/idgl-loop.md`. An expensive full gate is not a debugging
tool. After a red gate, isolate the first failing node/shape/config and run the
cheapest decisive diagnostic. If the same failure signature repeats, stop and
request replanning; never run the unchanged full gate again.
Read `references/experiment-budget-ladder.md` before optimize-stage work. It
defines the route-proof → microbaseline → timeline → leverage → focused-profiler
ladder and its stop conditions.

## Required order of work

1. **Read the repository contract.** Inspect `AGENTS.md`, `CONTRIBUTING.md`,
   `INSTALL.md`, `ENVs.md`, `README`, `pyproject.toml`, lockfiles, CI, tests,
   benchmark runners, backend registries, and reference implementations. Record
   the exact applicable instructions in `research/PROJECT_NATIVE_SETUP.md`.
2. **Pin the kernel contract.** Write `research/KERNEL_SCOPE.md` with the op/API,
   allowed and frozen files, target GPU, supported shapes/dtypes/options,
   correctness reference, benchmark command, and acceptance criterion. Check
   open upstream issues/PRs before choosing overlapping work.
3. **Query the professional tool registry before choosing infrastructure.** Do
   not rely on memory or a generic web search. Query relevant platform and
   bottleneck categories, for example:

   ```bash
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.environment_audit catalog \
     --list-categories
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.environment_audit catalog \
     --platform nvidia --category attention
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.environment_audit catalog \
     --platform nvidia --category communication
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.environment_audit catalog \
     --platform nvidia --category profiling
   ```

   Write `research/TOOLCHAIN_CANDIDATES.md` with the exact queries, maintained
   candidates found, installed/project-native candidates, legacy options
   excluded, and the shortlist. The registry is curated rather than magically
   exhaustive: if the operation has no credible candidate, search current
   primary sources and propose a registry update instead of silently inventing
   infrastructure.
4. **Choose infrastructure before installing it.** Write
   `research/INFRASTRUCTURE_REUSE_PLAN.md` containing:
   - repository-native install command and extras;
   - official benchmark/test entry points;
   - existing backend/fallback abstractions;
   - mature libraries/DSLs considered and why the selected one fits;
   - exact capabilities required from the environment;
   - anything custom that remains necessary and why no maintained primitive
     already solves it.
5. **Audit the actual runtime.** Run, from the same Python environment that will
   execute tests and benchmarks:

   ```bash
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.environment_audit collect \
     --project-root . --target-python .venv/bin/python \
     --require <implementation> --require profiling
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.environment_audit check \
     --project-root .
   ```

   Select the implementation capability: `torch`, `triton`,
   `tilelang`, `cuda_cpp`, or `cutlass_cute`. Add `profiling` and `sanitizer`
   when the task needs them. Replace `.venv/bin/python` with the exact Python
   used by the repository's tests/benchmarks. A red audit blocks implementation.
   Treat a published benchmark/SOTA source as frontier evidence, not as a usable
   baseline, until its declared lockfile or pinned dependency closure reproduces
   from a clean environment. Record advertised and cleanly reproducible bests
   separately. Inspect the selected code path's imports and resolve dependency
   conflicts before attributing a crash to the kernel idea.
6. **Repair environment without destabilizing it.** Prefer, in order:
   - the repository's documented extra/lockfile/container;
   - the repository's CI version matrix;
   - an isolated venv/container with exact compatible versions;
   - a pinned official upstream source revision when wheels do not support the
     target architecture.

   Never blindly upgrade torch, Triton, CUDA, or the whole environment to make
   one import pass. Re-run the audit after an environment change, not after an
   unchanged failed attempt. Record the
   commands and versions; do not record secrets.
7. **Reproduce the unmodified baseline.** Correctness first, timing second.
   Record `research/BASELINE_PROTOCOL.md` and
   `research/BASELINE_RESULT.json`: command, environment hash/versions, GPU,
   shapes, dtypes, warmup/autotune/JIT policy, synchronization, isolation,
   latency distribution, memory, and correctness result.
8. **Profile before selecting the mechanism.** Use the project's profiler and
   official benchmark. Classify the dominant limit: launch/CPU overhead,
   memory traffic, compute/tensor-core use, occupancy/latency, synchronization,
   compilation, or a multi-kernel boundary. If counters are unavailable,
   document that limitation and use derived roofline/timing evidence rather than
   pretending. Start with one path-aligned shape and the lowest-overhead timeline
   view. Record environment/bootstrap and JIT/autotune time separately from the
   warmed steady-state measurement. Record end-to-end median/spread and the
   selected kernel's timeline duration. Do not use duration from a multi-pass
   NCU counter replay as the selected kernel's wall-clock share: replay and
   metric collection can perturb it. Reserve focused counter sections for a
   target that has already passed the leverage gate.
   Before editing source or collecting a second expensive profile, write
   `attempts/<id>/LEVERAGE.json` with
   `python -m argus_skill.verticals.kernel_engineering.leverage_gate analyze`.
   Supply baseline/path evidence, end-to-end and target-kernel durations, the
   noise-bounded required total speedup, and a justified plausible kernel bound.
   Use `--help` and `references/experiment-budget-ladder.md` for the exact shape.
   If the verdict rejects the target, preserve a no-go attempt and choose a
   higher-leverage boundary. Do not edit the kernel merely because one generated
   subkernel has an interesting counter.
9. **Run hypothesis-driven attempts.** Each `attempts/<id>/` must preserve source
   diff/snapshot, short `CHANGES.md`, correctness output, benchmark output, and a
   compact `OUTCOME.json`. Generate the shape with:

   ```bash
   "${ARGUS_SKILL_PYTHON:-python}" -m \
     argus_skill.verticals.kernel_engineering.attempt_outcome template \
     --attempt-id <id> > attempts/<id>/OUTCOME.json
   ```

   Record `execution_status`, `failure_class`, and `idea_status` separately.
   Source editing is unlocked only when `LEVERAGE.json` says `proceed`, unless
   new evidence changes the measured end-to-end share or the required MDE.
   Use the full reviewed Engineer–Reviewer exploration budget (normally three
   rounds; follow the live `Round: x/y`) for a direction whose candidate is
   correct and path-covered but not yet faster. Try 1 builds the functional
   candidate; intermediate Tries use measured regression/profile evidence and
   current primary sources to change a material mechanism; the final Try
   implements the strongest remaining evidence-backed design. Do not spend
   later Tries on an unchanged rerun or cosmetic knob sweep.
   Before the run, check the ledger for an equivalent mechanism/config and write
   the one-line claim being tested. After the run, retain a compact result and
   reusable insight; do not paste raw logs into the next Engineer prompt.
   Before assigning `supported` or `refuted`, record baseline identity,
   candidate commit plus dirty-diff hash, and dispatch/trace evidence that the
   benchmark shapes actually entered the changed code path.
   A candidate can be performance-refuted without refuting the broader direction.
   Preserve the candidate no-go, then change the mechanism before endlessly
   sweeping knobs. A compile or runtime
   error must be classified:
   - environment/toolchain mismatch;
   - unsupported architecture/API;
   - implementation bug;
   - numerical-contract failure;
   - benchmark/infrastructure failure.

   Environment, dependency, toolchain, build-configuration, hardware-access,
   profiler-permission, benchmark-infrastructure, and measurement-infrastructure
   failures mean the idea was not validly tested. Keep `idea_status` as
   `untested` or `inconclusive`; never reject the mechanism from those failures.
   Validate the ledger with `attempt_outcome check --project-root .`. The full
   correctness suite is reserved for baseline/candidate certification; iterate
   with the focused failing case.
10. **Validate the retained candidate.** Cover forward/backward as applicable,
   fp16/bf16/fp32 policy, aligned and irregular dimensions, varlen/options,
   non-contiguous inputs when supported, determinism/races, memory, missing
   dependency/hardware fallback, and repeated isolated timing. Keep claims
   hardware- and shape-bounded.
11. **Prepare upstream evidence.** `RESULTS.md` must include exact commands,
    versions, raw correctness/latency summaries, uncertainty, regressions,
    fallback/dispatch boundary, limitations, and why the selected infrastructure
    was reused. Do not claim generic GPU speedup from one architecture.

## Infrastructure selection ladder

Use the smallest maintained layer that exposes the control needed:

1. Existing project op/backend and benchmark harness.
2. PyTorch/native vendor primitive (`torch`, cuBLASLt, cuDNN, SDPA,
   Transformer Engine) when it satisfies fusion/layout/numerical needs.
3. Existing specialist library (FlashAttention/FlashInfer/xFormers or the
   project's own shared kernels).
4. Triton/Gluon or TileLang for tile-level control and rapid iteration.
5. CUTLASS/CuTe DSL/cuTile/CUDA C++ when architecture-specific pipelines,
   tensor memory/TMA, warp specialization, clusters, or custom epilogues are
   the real lever.

Do not install every layer. Choose from the measured bottleneck and repository
contract, then prove the chosen layer is usable with the audit.

The machine-readable registry lives at
`argus_skill/verticals/kernel_engineering/references/specialized_tool_registry.json`.
For its selection policy and primary-source map, read
`argus_skill/verticals/kernel_engineering/references/toolchain-selection.md`.

## Training and RL boundary

If the benchmark is end-to-end training rather than a standalone kernel, first
identify the canonical training framework and install its supported stack.
Reuse nanoGPT/nanochat, TorchTitan, Megatron-LM/NeMo, DeepSpeed/Accelerate, or
the repository's trainer as applicable. For RL, evaluate veRL/OpenRLHF/TRL
before authoring rollout, distributed execution, checkpointing, and advantage
infrastructure. Custom infrastructure is justified only when the task itself is
to change that infrastructure or the maintained options cannot satisfy a
documented requirement.

## Failure semantics

- `execution_status`: did the experiment complete, fail, or become blocked?
- `failure_class`: environment/toolchain/infrastructure, implementation,
  numerical, measurement, or performance?
- `idea_status`: untested, inconclusive, supported, or refuted?

Environment/toolchain/infrastructure and invalid-measurement failures cannot
support or refute the idea. Only a completed, correctly configured experiment
with valid numerical or performance evidence can refute it.

Never compensate for a missing dependency with a fake fallback and call the
fallback the candidate.
