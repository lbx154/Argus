---
name: "B200 KernelBench Runtime"
description: "Operational playbook for B200 KernelBench/SOL runs: verify the configured B200 remote, scorer endpoint, frozen official scorer, artifact capture, and the common infrastructure/correctness traps before optimizing kernels."
---

# B200 KernelBench Runtime

## When to use

Use this skill when a task mentions B200, KernelBench, SOL, SOL-ExecBench,
`eval_solution.sh solutions`, `36_RMSNorm_`, a B200
scorer, or a GPU-kernel benchmark whose score comes from a frozen service.

Pair it with `SOL Kernel SOTA Optimization` for mechanism search and with
`SOL Kernel Hands-on Trace` when the engineer needs a failure-first exemplar.
This skill owns the **runtime and evidence gate**, not the kernel idea.

## Non-negotiable runtime contract

1. The frozen official scorer is the only source of truth. Local debug timing,
   `gpu_run.py`, self-timed CUDA events, or a manually edited score file are
   not accepted as benchmark results.
2. Prove the B200 and scorer are reachable before editing a kernel:

   ```bash
   export KERNELBENCH_REMOTE='<remote from mission manifest>'
   export KERNELBENCH_SCORER_URL='<scorer base URL from mission manifest>'
   test -n "$KERNELBENCH_REMOTE" -a -n "$KERNELBENCH_SCORER_URL"

   ssh "$KERNELBENCH_REMOTE" \
     'hostname; nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv,noheader'

   curl -fsS --max-time 5 "$KERNELBENCH_SCORER_URL/health"
   ```

3. If the scorer is down, restore the port-forward or write an infrastructure
   blocker. Do not optimize against a guessed harness.
4. Run official scoring with failure propagation:

   ```bash
   set -o pipefail
   mkdir -p results attempts
   ./eval_solution.sh solutions 2>&1 | tee attempts/<attempt>/official.log
   printf '%s\n' "$?" > attempts/<attempt>/official.exitcode
   ```

5. Preserve frozen files (`eval_solution.sh`, target/baseline/scorer configs,
   `sol_targets.json`, service bridge code). Edit only the allowed solution file
   named by the task.

## Known B200 facts to re-verify

These facts must be checked in the current mission rather than copied from a
historical deployment:

- The remote command and scorer URL come from the mission manifest or
  environment.
- The scorer backend must report `gpu: "NVIDIA B200"` and the expected
  benchmark problem set.
- A working scorer may still exit nonzero after printing a `RESULT` line if the
  local artifact directory is missing; create output directories and preserve
  exit codes.

## Common traps from the real trace

- **`tee` hides failures** unless `set -o pipefail` is active.
- **No `results/` directory** can make the scorer crash after emitting the
  useful line. Create directories before scoring.
- **`gpu_run.py` only sends the script body** in some harnesses; it does not
  sync local `solutions/` edits. Use the official scorer for acceptance.
- **Baseline files may not define `ModelNew`**. Confirm the required symbols
  before using a file as a candidate.
- **Axis mistakes can be numerically plausible but wrong**. For RMSNorm, a
  wrong reduction axis can pass compilation and still produce large error.
- **NVIDIA tools may be locked down**. If `ncu` is unavailable, fall back to
  ptxas/SASS diagnostics, roofline arithmetic from official time, and
  mechanism-isolation variants.

## A compile/runtime error is a bug to fix, not a dead end

A scorer `RUNTIME_ERROR` / `NO_TRACE` (or a Triton lowering failure) is almost
always a **fixable** compile/config bug — a wrong arch flag (`sm_100a` vs
`sm_90a`), a misused CUTLASS 3.x / CuTe API, a dependency/version mismatch in
`spec.dependencies`, or an unsupported codepath — **not** a verdict that the
mechanism is wrong. The harness surfaces the full error: a failed workload's
trace carries the complete traceback (the eval driver's `log`), and a
pre-workload crash returns the build/`nvcc` stderr (read the `server_error=` /
official-log output in full, not just the status word). **Read that full error
and fix the build.** A compile error on the *right* (SOTA) approach is closer to
a win than a correct-but-slow wrong one. Do NOT abandon a promising structural
path the first time it errors and then burn rounds re-tuning a mechanism that
already lost — treating a tooling failure as "this mechanism failed, switch" is
the single most common way a strong kernel idea dies in the log.

**Iterate compiles on a fast build-check, not the full scorer.** If the harness
exposes a fast compile/smoke path separate from scoring (e.g. a `/compile`
endpoint or a `compile_check.sh` that builds + runs ONE workload and returns the
full error in seconds), use IT to debug compile/runtime errors — edit → build-
check → read error → fix → repeat — and only run the full official scorer once
the kernel compiles and passes the smoke run. Burning a full 12-workload ×
50-iteration scoring run just to discover the build still fails wastes the round.

## You are not limited to Triton

The official scorer accepts `spec.languages` of `pytorch`, `triton`,
`cute_dsl`, `cutile`, `cudnn_frontend`, `cutlass`, `cudnn`, `cublas`, and
`cuda_cpp`. For GEMM- and attention-heavy kernels the vendor cuBLAS/SDPA path is
usually the floor, and **beating it generally requires REPLACING that GEMM with
a hand-written fused kernel** (CUTLASS 3.x / CuTe DSL / CUDA C++), not trimming
peripheral launch/tile knobs around a GEMM you left on cuBLAS. If you profiled
the GEMM as the limiter, attack the GEMM itself. When a reference `examples/`
tree ships with the benchmark (e.g. `cutlass/gemm`, `cute_dsl/...`,
`cuda_cpp/...`), study the *structure* of the fast solution in the target
language before writing your own — study the structure; the mechanism is your
call.

## Required evidence artifacts

Every accepted B200 benchmark mission must leave:

- `research/GROUND_TRUTH.md` or equivalent scorer contract:
  target problem, editable file, frozen files, command, baseline score.
- Attempt directory containing source snapshot, official log, exit code,
  checksum before/after, and a short verdict.
- If blocked: `INFRA_BLOCKER.md` with exact failing command, observed output,
  missing service/path, and what must be restored.
- If keeping a candidate: final official log showing correctness and score,
  plus proof that the live `solutions/<problem>.py` matches the kept snapshot.

## Recovery ladder

1. Check scorer health.
2. Check B200 SSH and GPU visibility.
3. Check Kubernetes/port-forward process for the scorer service.
4. Re-run a tiny baseline official score.
5. Only then launch a new optimization attempt.

If any rung fails, stop optimizing and record a blocker with the exact command
output. Waiting is acceptable; fabricated scores are not.
