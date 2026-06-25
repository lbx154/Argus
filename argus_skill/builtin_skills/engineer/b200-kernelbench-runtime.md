---
name: B200 KernelBench Runtime
description: Operational playbook for B200 KernelBench/SOL runs: verify the B200 SSH endpoint, scorer port-forward, frozen official scorer, artifact capture, and the common infrastructure/correctness traps before optimizing kernels.
category: benchmark-kernel-infrastructure
priority: high
version: 1
author_model: gpt-5.5
created_at: 2026-06-18T00:00:00+00:00
---

# B200 KernelBench Runtime

## When to use

Use this skill when a task mentions B200, KernelBench, SOL, SOL-ExecBench,
`eval_solution.sh solutions`, `36_RMSNorm_`, `argus-kbench-evalsrv`, a B200
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
   ssh -p 2231 -i ~/.ssh/id_ed25519 -o BatchMode=yes root@127.0.0.1 \
     'hostname; nvidia-smi --query-gpu=index,name,memory.used,utilization.gpu --format=csv,noheader'

   curl -fsS --max-time 5 http://127.0.0.1:2232/health
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

These facts were true in the recorded Argus workspace but must be checked in the
current session:

- B200 SSH often appears as `root@127.0.0.1 -p 2231`.
- KernelBench scorer health has been exposed at `127.0.0.1:2232/health`.
- The scorer backend has reported `gpu: "NVIDIA B200"` and 8 benchmark
  problems including `36_RMSNorm_`.
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

