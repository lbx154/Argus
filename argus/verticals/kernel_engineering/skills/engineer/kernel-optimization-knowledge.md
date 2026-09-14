---
name: "Kernel Optimization Knowledge & Retrieval"
description: "The math/physics priors a kernel optimizer must reason with (roofline, arithmetic intensity, memory bandwidth, occupancy, latency hiding) and HOW to retrieve what you don't know — diagnose the real bottleneck from measurements, search the right sources, and transfer ideas from adjacent domains. Optimize the mechanism, not the parameters."
---

# Kernel Optimization Knowledge & Retrieval

## When to use

Whenever you optimize a GPU kernel and need to decide *what to change and why*
— before touching code, and every time a change does not help. This skill is
the **reasoning layer**: it tells you which physical limit you are fighting and
where to learn what you don't yet know. Pair with `Official SOL-ExecBench
Environment` (how to measure) and `SOL Kernel SOTA Optimization` (mechanism
catalog).

**The cardinal rule:** never tune blindly. Every change must be justified by a
*measured* bottleneck and a *physical* model of why it should move the number.
Parameter sweeps without a model are noise; the win comes from changing the
**mechanism** (memory layout, what crosses DRAM, how work maps to SMs), not the
block size.

## Step 0 — find the physical limit (roofline first, always)

A kernel is bounded by ONE of: memory bandwidth, compute throughput, latency, or
launch/overhead. Decide which with arithmetic:

- **Arithmetic intensity** `AI = FLOPs / bytes_moved` (FLOP per byte of DRAM
  traffic).
- **Roofline ridge point** `AI* = peak_FLOPs / peak_BW`. If `AI < AI*` you are
  **memory-bound** (the only lever is *move fewer bytes* / better reuse); if
  `AI > AI*` you are **compute-bound** (the lever is *more efficient math* —
  tensor cores, fewer instructions, better ILP).
- **Speed-of-light time** `t_sol = max(FLOPs/peak_FLOPs, bytes/peak_BW)`. If your
  kernel is already near `t_sol`, stop — you are at the wall; only a *different
  algorithm* (fewer bytes or fewer FLOPs) helps.

**B200 anchors (SOLAR / SOL-ExecBench):** HBM bandwidth ≈ **8 TB/s**, bf16 dense
peak ≈ **1.811 PFLOP/s** → ridge `AI* ≈ 226 FLOP/byte`. Most elementwise /
norm / activation / attention-projection kernels sit far below the ridge ⇒
**memory-bound** ⇒ optimize *bytes moved*, not FLOPs.

Worked example: an elementwise op on an `N`-element bf16 tensor reads+writes
`4N` bytes (in + out, ×2 if it also reads a weight). `t_sol ≈ 4N / 8e12 s`.
If your measured time is 5× that, you are wasting 4× DRAM traffic — look for
redundant reads, un-fused passes, or unvectorized (sub-128-bit) loads.

## The levers, by bottleneck

**Memory-bound (the common case):**
- **Fuse passes** so an intermediate never round-trips through DRAM (the single
  biggest win — each eliminated pass removes a full read+write).
- **Vectorize global access** to 128-bit (`float4`/`uint4`/bf16×8). Sub-word
  loads waste bandwidth and transactions.
- **Coalesce**: consecutive threads must touch consecutive addresses; fix
  strided/transposed access with shared-memory staging or a better tile.
- **Reuse via shared memory / registers** to cut redundant global reads
  (the classic tiled-GEMM idea generalizes).
- **Pack** (e.g. bf16×4 in a `uint64`) to move more useful payload per
  transaction.

**Compute-bound:**
- Use **tensor cores** (wmma/CUTLASS/cuBLASLt/cuDNN) — hand FMA loops lose by
  10×+ on matmul/conv/attention.
- Raise **ILP / instruction efficiency**, cut redundant work, use fast-math
  where the official tolerance allows.

**Latency / occupancy-bound (small or serial kernels):**
- **Occupancy** = active warps / max warps per SM; too few warps ⇒ stalls aren't
  hidden. But occupancy is a *means*, not a goal — past "enough to hide latency,"
  more occupancy can *hurt* (register/shared-mem pressure). Check
  registers-per-thread and shared-mem-per-block against the SM budget.
- **Launch overhead** dominates tiny kernels → fuse, or use CUDA graphs /
  persistent kernels.
- **Tail effect**: grid not a multiple of SM count leaves SMs idle; pick grids
  that divide the machine.

## How to retrieve what you don't know

Common, reusable facts live **in this skill** (roofline, the B200 anchors, the
levers). For everything else, *go get it* — a strong optimizer is a fast learner,
not an omniscient one:

1. **Read the definition + measure first.** The `definition.json` gives exact
   shapes/dtypes ⇒ compute `AI`, `t_sol`, and the byte budget yourself. The
   harness gives `t_k`. The *gap* tells you the bottleneck — that is your
   research question, stated precisely, before you search.
2. **Profile when timing isn't enough.** `ncu`/`nsys` for achieved bandwidth,
   occupancy, stall reasons, L2 hit-rate. If hardware counters are locked
   (`ERR_NVGPUCTRPERM`), *derive* achieved BW = `bytes_moved / t_k` and compare
   to 8 TB/s — that alone tells you the memory-bound headroom.
3. **Search the right source, not just the web:**
   - mechanism/library: **CUTLASS** (GEMM/conv/epilogue fusion), **cuDNN
     frontend** (attention/conv), **cuBLASLt**, **Triton** docs + tutorials,
     **CUTe/cuTile** DSL examples (the official repo ships `examples/` for each).
   - concept/why: NVIDIA CUDA C++ Programming Guide + Best-Practices Guide,
     GPU MODE / lectures, the kernel's source paper (the `hf_id` in the
     definition often points at the model).
   - state-of-the-art: search "<operation> B200 / Hopper / Blackwell kernel",
     FlashAttention/FlashInfer for attention, recent arXiv for fused variants.
4. **Transfer from adjacent domains.** Most kernel wins are re-applications of a
   few cross-domain ideas: *blocking/tiling* (cache-oblivious algorithms),
   *operational intensity* (HPC roofline), *streaming/online algorithms*
   (Welford for variance, online-softmax for attention — compute stats in one
   pass instead of two), *mixed precision / error analysis* (numerical
   analysis — when does fp32-accumulate matter for the tolerance?), *Gaussian /
   special functions* (`ndtri`, `erf` — a physics/stats identity can replace an
   iterative solve). When stuck, ask: "what field already solved a
   bandwidth/precision/parallelism problem shaped like this?"

## Go deep — write the kernel, don't just tune it

External knobs (block size, num_warps, num_stages) are the *last* 5%. The win
is structural and lives in the kernel body:
- change **what crosses DRAM** (fuse, recompute-vs-store, keep it in
  registers/SMEM);
- change the **work decomposition** (row-per-block vs split-K, persistent grid,
  one-pass online stats);
- change the **numerics** (bf16 storage + fp32 accumulate, exact vs approx
  special functions within tolerance);
- change the **instruction mix** (tensor cores, vectorized/packed I/O).
When a sweep plateaus, that is the signal to *re-derive the bottleneck and
rewrite the mechanism*, not to sweep harder.

## Honesty (non-negotiable)

The official scorer with **cold L2 + locked clocks** is the only truth. A
"speedup" from a warm cache, a hidden stream, fewer real bytes than the spec, or
a degenerate output is not a speedup — the official harness flushes L2, clones
inputs, and rejects timing tricks (→ SOL 0). Optimize the real physical limit.
