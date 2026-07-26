# FLA Kernel Optimization — Argus `kernel_engineering` vertical case study

Autonomous GPU-kernel-optimization case study. Argus's `kernel_engineering` vertical
optimized the **`chunk_kda`** (Kimi Delta Attention) kernels in
[`fla-org/flash-linear-attention`](https://github.com/fla-org/flash-linear-attention)
on an **NVIDIA B200 (sm_100)**, reaching **+17.66%** certified at N>=10 and **+29.93%**
on a single combined verification run, at `B8_T1024_H8_D64` — correctness-preserving and
memory-neutral against a frozen baseline.

Argus performed the profiling, hypothesis, kernel implementation, benchmarking, and
independent certification autonomously; the operator only supplied the objective and
re-derived every speedup from the raw `score.json`.

**Status: submitted upstream, not accepted.** The work is under review as
[fla-org#1054](https://github.com/fla-org/flash-linear-attention/pull/1054), where a
maintainer has questioned whether a D64 result generalises at all. Read
*[Upstream status](#upstream-status)* before citing any number here — the speedups are
measured honestly, but their scope is one shape on one GPU generation.

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

## Upstream status

Submitted to the library itself as
[fla-org/flash-linear-attention#1054](https://github.com/fla-org/flash-linear-attention/pull/1054)
(2026-07-22). **Open, not merged**, no formal review as of 2026-07-26; CI is green
(12 passed / 11 skipped / 3 cancelled). An earlier attempt, #1053, was self-closed four
seconds after opening and carries no separate history. The upstream diff (+437/-31) is
slightly larger than the patch archived here (+432/-32); treat the PR as authoritative.

Maintainer `zhiyuan1i` — author of several merged KDA kernel PRs upstream (#672, #703,
#733) — called the fusion strategy sound and asked for two things before it can be
judged:

> Could you add numbers for **D=128 shapes (e.g. H32 / H64, D128)**? D=64 has very
> limited practical use for KDA, so the ~30% geomean at B8_T1024_H8_D64 is hard to
> extrapolate: at D128 each chunk does substantially more compute, so kernel-launch
> overhead and HBM round-trips weigh much less, and the register pressure of the
> solve-epilogue fusion also grows. [...] **Hopper (H100) numbers** would also be
> valuable — FLA's CI runs on H100, so that's the platform where most users will
> actually validate and run this.

**This critique is mechanistically consistent with our own "Mechanism theme" above, and
that is what makes it serious.** Every one of the three wins removes a kernel launch or
an HBM round-trip, so each is worth exactly as much as those fixed costs weigh in the
total. D64 does little compute per chunk, which is the regime where that weight is
highest. At D128 the same savings are amortised over more arithmetic, so the speedup
should be expected to shrink — by how much is unmeasured. The measurements here are
sound; what is unproven is that they generalise to the shape and the hardware the
library's users actually run.

Until D128 and H100 numbers exist, this case study demonstrates a correctness-preserving,
memory-neutral, independently certified optimisation **at B8_T1024_H8_D64 on B200** — and
nothing wider.

## Files

- `flash_linear_attention_kda_fusions.patch` — the combined diff vs `ccb0ff94` (5 files, +432/-32);
  it does **not** modify the evaluator, baseline, or any external repository state.
- `certified_results.json` — per-optimization and combined certified numbers.

## Caveats

1. **The headline number is the least certified one.** The **+29.93% combined** figure is a
   single frozen paired verification run (fwd + fwd+bwd). The three component optimizations
   are each **N>=10** certified; a full N>=10 certification of the combined stack would
   tighten it (expected ~+25–30%). The strongest number that clears the stated certification
   bar is the **+17.66%** cumulative of optimizations #2 and #3.
2. **Generalisation is untested.** See *Upstream status*: one shape, one GPU generation, and
   the mechanism predicts the gain shrinks at larger head dimensions.
