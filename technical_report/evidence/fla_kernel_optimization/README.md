# FLA Kernel Optimization — Argus `kernel_engineering` vertical case study

Autonomous GPU-kernel-optimization case study. Argus's `kernel_engineering` vertical
optimized the **`chunk_kda`** (Kimi Delta Attention) kernels in
[`fla-org/flash-linear-attention`](https://github.com/fla-org/flash-linear-attention)
on an **NVIDIA B200 (sm_100)**, reaching a combined **+29.93%** end-to-end speedup at D64
that is correctness-preserving and memory-neutral, verified against a frozen baseline.

Argus performed the profiling, hypothesis, kernel implementation, benchmarking, and
independent certification autonomously; the operator only supplied the objective and
re-derived every speedup from the raw `score.json`.

## Setup

- **Op / shape:** `chunk_kda`, `B8_T1024_H8_D64` (D64), bf16, forward and forward+backward.
- **Baseline:** flash-linear-attention @ `ccb0ff94` (frozen, immutable).
- **Hardware:** NVIDIA B200 (sm_100).
- **Measurement:** frozen paired baseline-vs-candidate evaluator (SHA-pinned scorer);
  `geomean_speedup = baseline_latency / candidate_latency`; peak memory as `max_mem_ratio`;
  atol 1e-2 / rtol 2e-2. Baseline and candidate are measured together on the same GPU so
  shared platform noise cancels in the ratio.
- **Certification bar:** correctness PASS + 0 CUDA errors + memory-neutral (`max_mem_ratio <= 1.00`)
  + N>=10 paired repeats + median geomean >= 1.05, with a cleared-cache repeat.

## Certified optimizations

Each optimization was independently certified vs `ccb0ff94` (numbers in `certified_results.json`):

1. **Paired q/k L2-norm fusion (forward + backward)** — the baseline issues two separate `l2norm`
   launches (q, k) in each direction; this fuses each pair into one kernel, halving launches and HBM
   traffic for the normalization step. **N=10 median +5.6%** (independent re-verification +8.1%).
2. **Inter-solve recompute-epilogue fusion** — `chunk_kda_fwd_kernel_inter_solve_fused` already holds
   the solved `Akk` in registers; computing `w/u/kg` in an epilogue removes the separate
   `recompute_w_u_fwd_kda_kernel` launch and its `Akk` HBM reload. **N=10 median +7.27%** (cold-cache +8.8%).
3. **Cumsum-into-intra producer fusion** — the chunk-local cumulative gate `g` is computed inside the
   intra-subchunk producer instead of a standalone `chunk_local_cumsum_vector_kernel` launch, stored
   once for reuse. **+10.4% increment over #2** (cumulative with #2 = **+17.66%**).

**Combined (all three):** **+29.93% geomean**, correctness PASS, memory-neutral (`max_mem_ratio 1.00`),
no CUDA errors, on a frozen paired verification run.

## Mechanism theme

Every win **eliminates a kernel launch and/or an HBM round-trip** in the `chunk_kda` forward pipeline
(fusion / launch-count reduction). Autotune-config search and memory-neutral micro-transforms stayed
within measurement noise, and the dominant backward kernel resisted fusion — so the gains came
consistently from forward producer→consumer fusion. Speedups compound multiplicatively when stacked.

## Files

- `flash_linear_attention_kda_fusions.patch` — the combined diff vs `ccb0ff94` (5 files, +432/-32);
  it does **not** modify the evaluator, baseline, or any external repository state.
- `certified_results.json` — per-optimization and combined certified numbers.

## Caveat

The **+29.93% combined** figure is a single frozen paired verification run (fwd + fwd+bwd). The three
component optimizations are each **N>=10** certified; a full N>=10 certification of the combined stack
would tighten the combined figure (expected ~+25–30%).
