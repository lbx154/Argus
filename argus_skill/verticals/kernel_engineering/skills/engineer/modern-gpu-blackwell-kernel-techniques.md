---
name: "Modern GPU Programming for MLSys (Blackwell B200) — the SOTA kernel reference"
description: "A distilled reference to Tianqi Chen / MLC's free book \"Modern GPU Programming For MLSys\", the current SOTA guide for writing fast ML kernels on Blackwell (sm_100a / B200) — GEMM, FlashAttention, tensor cores, TMA async data movement, persistent CTAs, warp specialization, 2-CTA clusters, online softmax. When you optimize a B200 kernel, CONSULT this book (read the relevant chapter online) instead of guessing; the concepts transfer to Triton / CUTLASS / cuda_cpp even though the book's examples use the TIRx DSL."
---

# Modern GPU Programming for MLSys (Blackwell B200)

**Don't guess B200 kernel design from memory — read the SOTA reference and build from
it.** The free book *Modern GPU Programming For MLSys* (Tianqi Chen / MLC, CMU) is the
current best guide to fast ML kernels on **Blackwell (`sm_100a`, B200)**. When a task is
on B200, open the relevant chapter and steal its mechanism. This is the self-evolution
principle in action: **search → read SOTA → build on it**, not closed-room guessing.

- Book: https://mlc.ai/modern-gpu-programming-for-mlsys/
- Source + reference kernels: https://github.com/mlc-ai/modern-gpu-programming-for-mlsys

The examples are in the **TIRx DSL** (Apache TVM's `tvm.tirx`), but the SOL-ExecBench
scorer wants triton / cutlass / cuda_cpp / cute_dsl / cudnn / pytorch — so read the book
for the **mechanism and the layout/scheduling reasoning**, then implement it in whichever
language the eval accepts. The physics transfers; the syntax doesn't.

## The Blackwell kernel playbook (distilled from the book's structure)

**Part I — Understand the machine first (this is your roofline + the levers).**
Blackwell has: a deep memory hierarchy incl. **Tensor Memory**; **TMA** (Tensor Memory
Accelerator) for *asynchronous* bulk data movement that overlaps with compute; **tensor
cores** driven by warpgroup MMAs; **warpgroups / clusters** (2-CTA clusters share data);
and async coordination (CLC scheduling). The whole game is **overlap**: keep the tensor
cores fed by moving the next tile via TMA while the current tile computes. Always model
roofline + overlap before writing anything.

**Part III — GEMM, tiled to SOTA (the recipe for anything GEMM-heavy).**
A fast Blackwell GEMM is built progressively:
1. **Tile** the problem (M/N/K blocking to fit SMEM / Tensor Memory).
2. **TMA pipelining** — async-copy the next A/B tile while the current MMA runs
   (multi-stage software pipeline; the single biggest lever).
3. **Persistent CTAs** — one CTA stays resident and walks many output tiles, amortizing
   launch + avoiding the tail effect.
4. **Warp specialization** — dedicate some warps to TMA/data-movement and others to MMA,
   so the two genuinely overlap.
5. **2-CTA clusters** — two CTAs cooperate (share a tile via distributed SMEM) to cut
   redundant loads.
Apply this to MoE expert GEMMs, MLP projections, lm_head, any matmul-bound kernel.

**Part IV — FlashAttention 4 (the recipe for any attention/decoder kernel).**
The flash pattern, built from the GEMM techniques: **two MMAs with a softmax between
them** (QKᵀ → softmax → ·V), with **online-softmax rescaling** so you never materialize
the seq×seq scores (one pass, exact, low memory), plus **causal masking** and **GQA**
(expand/replicate KV heads cheaply). For fp32-tolerance kernels, run the MMAs in TF32 on
tensor cores while keeping the online softmax in fp32 (see the measurement-integrity /
optimization-process skills). This is the mechanism behind every fast attention/decoder
kernel in this benchmark.

## How to use it on a SOL-ExecBench kernel

1. Classify the kernel: **GEMM-heavy** (MoE, MLP, projections, lm_head) → Part III recipe;
   **attention/decoder** (GQA, RoPE, flash) → Part IV recipe; **memory-bound** (norm,
   activation, elementwise) → roofline says cut DRAM traffic (fuse + TMA + vectorize).
2. **Open the matching chapter**, read how it structures the tiling / pipelining /
   overlap, and what the binding limit is.
3. Implement that mechanism in triton / cutlass / cuda_cpp / cute_dsl (whichever fits) —
   the book's reasoning tells you *what* to build; pick the language by the bottleneck.
4. Cross-check the reference kernels in the repo for the concrete tiling/stage counts to
   try first, then tune from a measured profile (not vibes).

Pair with: `Kernel Optimization Knowledge & Retrieval` (roofline + retrieval), `Kernel
Optimization Process — Worked Trace` (the diagnose→write-kernel loop), `Kernel Benchmark
Measurement Integrity` (so the speedup you measure is real), `Official SOL-ExecBench
Environment` (how to score it).
