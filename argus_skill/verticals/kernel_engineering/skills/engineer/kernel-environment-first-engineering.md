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

   Select at least one implementation capability: `torch`, `triton`,
   `tilelang`, `cuda_cpp`, or `cutlass_cute`. Add `profiling` and `sanitizer`
   when the task needs them. Replace `.venv/bin/python` with the exact Python
   used by the repository's tests/benchmarks. A red audit blocks implementation.
6. **Repair environment without destabilizing it.** Prefer, in order:
   - the repository's documented extra/lockfile/container;
   - the repository's CI version matrix;
   - an isolated venv/container with exact compatible versions;
   - a pinned official upstream source revision when wheels do not support the
     target architecture.

   Never blindly upgrade torch, Triton, CUDA, or the whole environment to make
   one import pass. Re-run the audit after every environment change. Record the
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
   pretending.
9. **Run hypothesis-driven attempts.** Each `attempts/<id>/` must preserve source
   diff/snapshot, short `CHANGES.md`, correctness output, benchmark output, and a
   verdict. Change the mechanism before endlessly sweeping knobs. A compile or
   runtime error must be classified:
   - environment/toolchain mismatch;
   - unsupported architecture/API;
   - implementation bug;
   - numerical-contract failure;
   - benchmark/infrastructure failure.

   Fix environment-class failures before rejecting the mechanism.
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

- Missing package/compiler/profile permission: environment blocker.
- Baseline cannot reproduce: setup or protocol blocker.
- Candidate fails only under a different environment: invalid comparison.
- Candidate compiles but violates the oracle: implementation/numerical failure.
- Candidate is correct but not faster: valid negative result; update diagnosis.
- Candidate is faster only under contention/warm cache: measurement failure.

Never compensate for a missing dependency with a fake fallback and call the
fallback the candidate.
