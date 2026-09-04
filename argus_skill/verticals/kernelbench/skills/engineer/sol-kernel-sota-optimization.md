---
name: "SOL Kernel SOTA Optimization"
description: "A senior-researcher methodology for KernelBench/SOL-ExecBench GPU kernel optimization — the machine as throughputs+latencies, the bottleneck taxonomy, counter-free diagnosis by micro-benchmark isolation (when ncu is locked), an optimization toolkit ordered by leverage (algorithm first, micro-opts last), the Hopper/Blackwell pattern library, operator→bottleneck→structure priors, and the experimental discipline. Distilled human expertise to learn, not a kernel to copy."
---

## Title
SOL Kernel SOTA Optimization

## What this is
This is **distilled human expertise** — how a senior GPU-kernel researcher actually
thinks, not a checklist. Read it to acquire the mental model and the priors; do not
treat it as steps to mechanically execute or a kernel to transplant. The numbers and
one worked example exist to teach the *method*; the method generalizes to any kernel.
When a real kernel teaches you something sharper, evolve this. (Companion:
`SOL Kernel Hands-on Trace` is one fully-worked, failure-first example of the loop.)

## When to use
- The objective mentions KernelBench, SOL-ExecBench, SOL score, speed-of-light,
  CUDA/Triton/CUTLASS kernels, B200/H100/A100 timing, or speedup over PyTorch eager.
- The task names an editable kernel file, a frozen scorer, and a numeric speed metric.

## When NOT to use
- A paper benchmark matrix, multi-family agent evaluation, or publication evidence run.
- The scorer is missing and cannot be reconstructed — write a setup/blocker report
  first; do not invent a metric.

---

## 0. Research-first: measure the bottleneck, but RETRIEVE the implementation

This skill's spine is "the decisive insight is **measured**, not known" — and for the
*bottleneck* that is right: you do not read a paper to learn RMSNorm is memory-bound, you measure
its bandwidth against the roofline (§3–§5). But a second axis runs the other way: the
**implementation of the winning structure** — the exact MMA tile shapes and operand layouts, the
SMEM **swizzle** a `wgmma`/`tcgen05` wants, the TMA descriptor, the flash-attention tiling — is
specialized, architecture-specific human knowledge your parametric memory gets **wrong**.
**Diagnose by measuring; implement by retrieving the canonical pattern.**

You have a sharp reason to retrieve: your knowledge is frozen at cutoff, capacity-bounded, and
weakest on exactly this long tail (Blackwell `tcgen05`/TMEM layouts, CUTLASS swizzles, FA3
internals — much of it post-cutoff), and you are trained to sound confident, so from memory you
will emit a plausible-but-wrong layout that **compiles, runs, and is silently slow or wrong**. The
evidence is blunt: frontier agents measurably fail to re-implement even known kernels/gains
(Automated LLM Speedrunning / agentic-research benchmarks). The binding loop is **measure →
retrieve the pattern → implement → re-measure**.

**Invention is recombination** (Nature 2022): the winning kernel is the known canonical structure
(flash-attention tiling, a CUTLASS GEMM mainloop, a fused epilogue) **specialized to your shape**,
not a never-seen algorithm. Retrieve the canonical form; don't re-derive it from memory and get
the swizzle wrong.

**The discipline:** (1) measure the bottleneck first (§3–§5) — do not "research" what you can
measure; (2) for the fix, retrieve the most concrete reference (a CUTLASS example, the official
kernel repo, the NVIDIA programming guide, the arch's PTX/ISA doc) — code > prose; (3) corroborate
any quoted peak/throughput against a **measured** copy-kernel (§4), never the spec sheet; (4)
implement one change, re-measure against the frozen scorer.

**Anti-cheat line:** retrieve general technique + the architecture's documented patterns. NEVER
retrieve this task's answer kernel. Understanding *why* flash-attention tiles is research; copying
the answer is disqualifying.

**Search playbook (general technique / arch docs only):**
- canonical structure: CUTLASS GEMM + epilogue examples; FlashAttention IO-aware tiling (FA3, H100/B200); online softmax
- the arch's MMA: Hopper `wgmma` operand layout + SMEM swizzle; Blackwell `tcgen05` + TMEM + TMA descriptors; the right low precision + scaling (MXFP/NVFP4)
- the method: roofline / arithmetic-intensity / ridge point; MFU; diagnosing without `ncu` counters (`ERR_NVGPUCTRPERM`) via CUDA-event timing + analytic FLOP/byte; reading SASS for vectorization / spills / actual `HMMA`/`wgmma` issue
- the workflow: Nsight Systems → Nsight Compute top-down; `torch.compile` fusion + CUDA graphs for launch-bound kernels

---

## 1. The mental model: a chip is throughputs and latencies

Every kernel is a flow of bytes and instructions through a hierarchy of **throughput
resources** (each with a peak rate) and **latencies** (each hidden only by
concurrency). The whole craft reduces to three moves:

- **Throughput resources** (each can be the wall): HBM bandwidth, L2 bandwidth, shared
  memory bandwidth, register-file bandwidth, the tensor-core (MMA) pipe, the FMA/ALU
  math pipes, the load/store unit (LSU/MIO), and the instruction *issue* slots.
- **Latencies** (hidden by having enough work in flight): HBM ~400–800 cycles, L2
  ~200, shared memory ~20–30, a dependent FMA ~4–6, an MMA instruction tens of cycles,
  a `__syncthreads` the slowest warp.

A kernel **saturates exactly one resource and wastes the rest**. Optimization is:
1. find the saturated resource (the bottleneck),
2. reduce *demand* on it (less traffic / fewer instructions / less work), or
3. raise its *effective supply* (more in flight to hide latency; move work to an idle
   resource; use a wider/faster unit).

Everything below is in service of those three moves. **Little's law** governs (3):
needed concurrency = latency × throughput. If you cannot keep that many
bytes/instructions in flight, you are latency-bound no matter how fast the units are.

---

## 2. Honesty rules (non-negotiable, the only hard floor)

- The **frozen scorer** is the only source of truth for SOL and correctness. A
  self-timed number is a hypothesis, not a result.
- **Correctness on the scorer's randomized inputs** comes before any timing. A fast
  wrong kernel scores 0. Never hard-code shapes/values beyond the API contract; the
  scorer randomizes inputs precisely so you cannot.
- **No fabricated numbers.** Every bandwidth/%peak you write is arithmetic on a real
  scorer-measured time over a byte count from the fixed API contract — never invented,
  never nudged to make a roofline close.
- Reproduce baselines **once** on the same hardware/harness; label "SOTA-oriented" unless
  the protocol matches a real leaderboard. After that, a scorer-verified best is a **fixed
  floor, not a hypothesis** — do NOT re-score or re-audit old attempts to re-confirm it.
  Reproducibility is not the goal and small run-to-run jitter is not regression; spend the
  round shipping a **new mechanism that beats the floor**, then score it once.

---

## 3. Diagnosis: which wall? (the bottleneck taxonomy)

Optimizing the non-bottleneck is the #1 way to waste a day. Classify first. The
categories worth separating (distilled here for diagnosis, not a standard named
taxonomy) and their tells:

| Wall | What saturates | Tell (from timing + roofline) | First move |
|---|---|---|---|
| **HBM-bound** | DRAM bandwidth | effective_BW near peak; high arithmetic intensity not reached | fuse, tile for reuse, vectorize loads |
| **L2/cache-bound** | L2 BW / working set spills | BW between HBM-peak and SMEM; reuse distance > L2 | tile to fit L2; reorder for locality |
| **Latency-bound / under-occupied** | nothing — stalls waiting | low BW **and** low FLOP/s **and** low occupancy together | more warps **or** more ILP per thread |
| **Compute-bound (math pipe)** | FMA/ALU issue | achieved FLOP/s near non-TC peak | reduce ops, use intrinsics, lower precision |
| **Tensor-core-bound** | MMA pipe | achieved TC FLOP/s near TC peak | the good place; only layout/pipeline left |
| **Issue/overhead-bound** | instruction issue slots | many instructions per useful FLOP (address calc, predication) | unroll, hoist, precompute addresses |
| **Occupancy-capped** | regs/SMEM cap warps | time drops sharply when regs/SMEM reduced | cut register/SMEM pressure, or add ILP |
| **Sync/atomic-bound** | barriers/atomics serialize | time scales with `__syncthreads`/atomic count | fewer barriers, warp-level prims, privatize |
| **Launch-bound** | kernel launch (~few µs) | tiny work; time ≈ constant regardless of size | fuse, persistent kernel, CUDA graphs |

The one people miss: **latency-bound looks like nothing is busy** — BW, FLOP/s, and
occupancy are *all* low at once. You are not saturating anything; you are waiting on
dependent chains or too few warps. The fix is concurrency (occupancy or ILP), not a
faster unit.

---

## 4. Diagnosis without hardware counters (the ncu-locked reality)

On shared B200 pods `ncu` is blocked (`ERR_NVGPUCTRPERM`): no occupancy/stall/throughput
counters. A senior researcher does not give up — **you reconstruct the roofline by
isolating one variable at a time with micro-benchmarks**, each run through the project
debug path (e.g. `gpu_run.py`). This is the heart of counter-free diagnosis:

- **Peak HBM BW:** a streaming copy/saxpy kernel with many CTAs, large data → achieved
  GB/s. This is your roofline denominator; do not trust the spec sheet.
- **Launch overhead:** an empty kernel at the *same grid* → the floor cost per launch.
- **Read vs write:** a read-only (reduction-to-scratch) and a write-only (memset-pattern)
  variant → which direction dominates, and whether writes are coalesced.
- **Is it compute or memory?** keep the exact memory access pattern but replace the math
  with a trivial op. Time unchanged → memory-bound; time drops → the math mattered.
- **Occupancy/latency:** sweep block size and `__launch_bounds__`/register cap. Large
  time swings → latency/occupancy-bound; flat → you are saturating a throughput unit.
- **Read the SASS** (`cuobjdump -sass` / `nvdisasm`): did it vectorize (`LDG.E.128`)?
  register count and **spills** (`STL`/`LDL` = local memory = a bug to fix)? did it
  emit the tensor-core ops (`HMMA`/`IMMA`/`wgmma`/`tcgen05`)? did it predicate a hot
  loop? The SASS is ground truth for "did the compiler do what I think".
- **Nsight Systems timeline** (`nsys profile`): even when `ncu` counters are locked, the
  nsys timeline usually still gives per-kernel durations, the gaps between them
  (launch/sync bubbles), and — with `--gpu-metrics-device` — sampled SM occupancy and
  memory throughput. Try it before assuming no profiling is possible; use it to
  cross-check the micro-benchmark story.
- **The reference ceiling:** always run `torch.compile`, cuBLAS/cuDNN, or a known-good
  kernel on the same shape. It tells you *absolute* headroom, not just relative.

Attribute the gap to **one** cause before choosing the next move. Never leave an "it's
launch *or* uncoalesced writes" hypothesis — that is a measurement you skipped.

---

## 5. Compute the roofline yourself

- **SOL-minimal bytes** = `numel × dtype_bytes × (reads + writes)`, from the kernel's
  **fixed shape/dtype API contract** (not from input statistics/sparsity — those can be
  cherry-picked). Read-once/write-once elementwise/reduction = `2 × numel × dtype_bytes`.
- **Effective BW** = `SOL-minimal bytes / cand_ms`, `cand_ms` = the **frozen scorer's**
  time (never a divergent debug time).
- **Arithmetic intensity** = useful FLOPs / bytes moved. Compare to the **ridge point**
  = peak FLOP/s ÷ peak BW. Below ridge → memory-bound; above → compute-bound. On B200
  (~8 TB/s HBM, ~2.25 PFLOP/s dense BF16 → ridge ≈ 280 FLOP/byte) almost every
  elementwise/reduction/norm/softmax kernel is deep in memory-bound territory; only
  large GEMM/conv/attention reach compute-bound.
- **%peak** is your *diagnostic for remaining headroom*; it never feeds the graded SOL.
  Defend the peak (measure it, §4), state how.

---

## 6. The optimization toolkit, ordered by LEVERAGE

Apply in this order. The biggest wins are at the top; the menu of micro-opts at the
bottom is where juniors start and seniors finish. **Ask "what is the minimum work this
problem requires?" before "how do I make this code faster?"**

1. **Change the algorithm / reduce the work** (10× lives here).
   Recompute vs store (flash attention recomputes the score matrix instead of writing
   N² to HBM); avoid materializing intermediates; exploit structure (symmetry, low
   rank, contract-guaranteed sparsity); a cheaper algorithm (Winograd cuts conv FLOPs;
   online/streaming softmax avoids a pass). The kernel that does less usually beats the
   kernel that does the same work faster — *provided* the lower-work variant does not
   wreck locality or add synchronization that costs more than it saves (confirm on the
   roofline, do not assume).
2. **Reduce traffic to the bottleneck resource.**
   Fusion (kill HBM round-trips between ops); **tiling for reuse** (the GEMM lesson:
   O(N³) work over O(N²) data — tile so each loaded element is reused O(tile_dim)
   times, raising arithmetic intensity past the ridge); keep data in the fastest tier
   it fits (registers > shared > L2 > HBM); recompute a cheap value rather than reload it.
3. **Raise effective bandwidth / feed the units.**
   Coalesce (adjacent threads → adjacent addresses → 128-byte transactions; vectorize
   to `LD/ST.128`); kill shared-memory **bank conflicts** (32 banks × 4B): padding
   fixes simple cases but leaves residual conflicts on non-power-of-2 tiles, so GEMM/
   attention use an XOR **swizzle** of the SMEM index (the layout the `wgmma`/`tcgen05`
   operand wants — CUTLASS has the canonical patterns) and the right swizzle differs by
   architecture; verify with a padded-vs-swizzled SMEM micro-bench. Then **async copy**
   (`cp.async` Ampere+, **TMA** Hopper/Blackwell) to overlap load with compute, and
   **double/triple buffer** (software pipelining) so the next tile loads while this one
   computes.
4. **Feed the tensor cores** (any matmul-shaped work).
   Right MMA tile shapes and operand layouts (K-major, the swizzle the instruction
   wants); warpgroup MMA (`wgmma`, Hopper) / 2-SM MMA (`tcgen05`, Blackwell);
   producer/consumer **warp specialization** with TMA — *but only when load and compute
   genuinely overlap*: if you are already TMA-bandwidth-bound, dedicated producer warps
   just idle, so specialize only when the roofline says compute has slack to hide the
   loads behind. Then persistent kernels + a good tile schedule, and **epilogue fusion**
   (bias/activation/scale folded in). If a GEMM is not on the tensor cores, nothing else
   you do matters.
5. **Hide latency** (§1, Little's law).
   Enough warps (occupancy) **or** enough ILP per thread. Register tiling makes this
   concrete: a thread computing a T×T register tile does T² FMAs per 2T loads → it
   reuses each load ~T times in registers (intensity ∝ T) and exposes T² independent
   FMAs of ILP, at a cost of ~T² registers that cap warps. Grow T until ILP alone covers
   the FMA latency (`in_flight ≥ latency × issue_rate`) or registers **spill**
   (`STL`/`LDL` in SASS) — the sweet spot is the largest tile that does not spill; past
   it, add warps, not tile size. This is the Volkov subtlety: *lower* occupancy with
   heavy register blocking can beat high occupancy because ILP also hides latency and
   registers buy reuse. Occupancy is a means, never the goal.
6. **Cut instruction overhead.**
   Unroll hot loops, precompute/strength-reduce addresses, `__restrict__` + read-only
   path (`__ldg`) for inputs, minimize predication, hoist loop invariants.
7. **Numerics as a lever** (within the correctness tolerance — it is a gate, precision
   is a dial inside it). fp32-accumulate + bf16/fp16 storage; TF32 for matmul; FP8/FP4
   with per-tensor or microscaling (Blackwell MXFP) when tolerance allows; fast
   intrinsics (`__expf`, `rsqrtf`). Halving bytes roughly doubles a memory-bound kernel
   — but FP8/FP4 microscaling is a *structural* change, not a free dial: per-block scale
   handling adds overhead, so expect ~1.5–1.8× on HBM-bound ops unless you fuse the
   scaling into a neighbor. Fix layout/traffic first; precision is orthogonal.
8. **Launch & scheduling.**
   Fuse into fewer larger kernels; persistent kernels for tiny repeated work (worth the
   complexity only when launch overhead dominates or the grid is not a clean multiple of
   waves — and watch for register spills from the resident loop); CUDA graphs to amortize
   launch; size the grid for full **waves** — beware **wave quantization / the tail
   effect** (a partial last wave can halve effective throughput).

---

## 7. Architecture pattern library (the priors)

The general law across generations: each new GPU adds **(a)** a faster/wider tensor
core, **(b)** better *async data movement* to feed it, **(c)** lower precision with
scaling. So for GEMM the bottleneck keeps migrating from *compute* to *getting data to
the compute in time* — which is why `cp.async`/TMA/pipelining/warp-specialization matter
more every generation.

- **Ampere (A100):** 3rd-gen TC, `cp.async`, TF32/BF16, 40–80 GB HBM2e (~1.5–2 TB/s).
- **Hopper (H100):** 4th-gen TC, **WGMMA** (warpgroup = 4 warps = 128 threads issue one
  MMA), **TMA** (async bulk tensor copy, frees threads from address math), **thread-block
  clusters + distributed shared memory** (CTAs in a cluster read each other's SMEM), FP8
  (E4M3/E5M2), ~3.35 TB/s HBM3.
- **Blackwell (B200):** 5th-gen TC via **`tcgen05`** with **TMEM** (dedicated tensor
  memory) and **2-SM (CTA-pair) MMA**; **FP4/FP6/FP8 with microscaling** (MXFP/NVFP4);
  ~8 TB/s HBM3e; dense BF16 ~2.25 PFLOP/s, multiplying for FP8/FP4. *Verify exact peaks
  on the card / datasheet rather than trusting any single quoted figure.*

Implication for B200 work: norms/elementwise are HBM-bound (chase the ~8 TB/s floor via
fusion + vectorization); GEMM/attention want `tcgen05` + TMA + pipelining and the right
low precision with scaling. Do not port a Hopper `wgmma` kernel to B200 unchanged:
`tcgen05` issues the MMA across a **2-SM (CTA pair)** with operands staged in per-SM
**TMEM** (distinct from Hopper's cluster-distributed SMEM), so operand layout, tile
shape, and K-tiling constraints differ — re-derive them on B200, do not transplant.

---

## 8. Operator → bottleneck → canonical structure

Internalize these so the roofline *confirms* rather than *discovers*:

| Operator class | Bottleneck | Canonical winning structure |
|---|---|---|
| Elementwise (ReLU/add/GELU) | HBM-bound (2–3N traffic) | fuse into neighbors; vectorized load/store; the only win is fusion |
| Reductions / norms (softmax, layernorm, rmsnorm, L1/L2) | HBM-bound, multi-pass eager | fuse passes into one; warp/block coop reduction vs thread-per-row by row length; **online softmax** when you cannot hold the row |
| GEMM / matmul (large) | tensor-core-bound | tile for reuse + feed TC + pipeline (TMA/wgmma); rarely beat cuBLAS without special shape/fusion |
| Conv | GEMM-shaped | implicit GEMM, or **Winograd** (fewer FLOPs) for 3×3 |
| Attention | HBM + SMEM-capacity bound | **flash-attention**: tile QKV, online softmax, never materialize N², recompute in backward |
| Scan/sort/scatter | latency / atomics / irregularity | work-efficient scan (Blelloch), privatization, warp prims — a different toolkit |
| Small / batched ops | launch + latency-bound | batch/persistent kernels, CUDA graphs |

A senior researcher reads "RMSNorm over C=64, huge spatial" and *already knows*:
memory-bound channel reduction, eager wastes ~5 passes, fuse to read-once/write-once,
one position's 64 channels fit in registers → no cross-thread reduction needed. The
roofline then *quantifies* the gap; it does not reveal the category.

---

## 9. The experimental discipline + the measured causal chain

Run the search like an experimentalist:

- **One variable per experiment.** Isolate with the §4 micro-benchmarks; attribute the
  gap to one cause before the next move.
- **Always carry the reference ceiling** (cuBLAS/torch.compile/roofline) so you know
  absolute distance to the limit, not just relative deltas.
- **Know the irreducible floor and stop there.** cuBLAS-optimal GEMM, a kernel already
  at ~95% of SOL, a latency-bound tiny op — recognize when the remaining gap is the
  hardware, not your code, and say so instead of grinding noise.
- **Correctness (randomized) → roofline → one mechanism → re-measure → attribute.**

Record each iteration as the **measured causal chain** — the artifact that lets anyone
reconstruct your reasoning (see `SOL Kernel Hands-on Trace` for a filled example):

```text
ITER n — <candidate>
  measured:    cand_ms (scorer), SOL (scorer); effective_BW = bytes/cand_ms = P% of peak
  read:        <what the % means in one line>
  bottleneck:  <which wall, from the numbers + isolation micro-bench>
  mechanism:   <the single change, and why it attacks that wall>
  re-measured: cand_ms (scorer), SOL (scorer); effective_BW = P'% of peak
  gap-to-SOL:  <distance to opt_ms/SOL floor> ; at P'% of peak, headroom below
  → next:      <next hypothesis, chosen by where the measured gap is>
```

Lightweight persisted artifacts (for the multi-round harness, not the research itself):
`research/GROUND_TRUTH.md` (scorer command, hardware, correctness rule, roofline,
baseline), `attempts/<name>/CHANGES.md` (the chain block + raw scorer log), `RESULTS.md`
(final table ranked by SOL, honest about wins and losses).

---

## 10. Worked example (compact)

KernelBench `36_RMSNorm_` on B200, shape `(112,64,512,512)` fp32, frozen scorer
`./eval_solution.sh solutions`. Roofline: SOL-minimal = `2·numel·4 = 15.03 GB`; peak
cross-checked via `opt_ms`: `15.03 GB / 1.879 ms = 8.0 TB/s`.

```text
ITER 0 — baseline eager
  measured:    8.898 ms, SOL=0.498; eff_BW = 15.03 GB / 8.898 ms = 1.69 TB/s = 21% of peak
  read:        21% → ~5× redundant HBM traffic (square/mean/rsqrt/divide each a pass)
  bottleneck:  HBM-bound; win = remove redundant round-trips, not faster math
  mechanism:   fuse to one kernel, one thread per (b,s), 64 ch in registers, read x once/write y once
  re-measured: 2.258 ms, SOL=0.893; eff_BW = 6.66 TB/s = 83% of peak
  gap-to-SOL:  floor opt_ms=1.879 ms (8.0 TB/s); at 83%, 17% headroom below
  → next:      isolate the 17% (write-only micro-bench vs empty-launch) before guessing
               coalescing vs occupancy; then warp-coop or float4 channel access
```

This is `fusion` (toolkit tier 2) chosen because the roofline said HBM-bound with ~5×
redundant traffic — not a block-size tweak (tier 6) and not tensor cores (no matmul). A
concrete realization is in `SOL Kernel Hands-on Trace`; derive your own from your numbers.

---

## 11. Senior-level mistakes (sharper than "common pitfalls")

- Optimizing the non-bottleneck (tuning block size before diagnosing the wall).
- Treating occupancy as the goal instead of a latency-hiding means.
- Materializing intermediates that fusion or recompute would eliminate.
- Leaving an "A *or* B" bottleneck hypothesis instead of isolating it.
- Ignoring wave quantization — a partial last wave silently halving throughput.
- Not reading SASS to confirm vectorization / no register spills / actual TC issue.
- Declaring a win from a warm-cache, un-randomized, or self-timed run.
- Grinding noise near the irreducible floor instead of moving to the next kernel.
- "Researching" a problem class you already know the answer to, instead of measuring.

## Response shape
- Target kernel, scorer command, hardware, and the defended roofline (peak + how).
- The bottleneck wall and the isolation evidence for it.
- The measured causal chain for each iteration (real scorer numbers only).
- Absolute distance to the reference ceiling / SOL floor, and whether it is worth more.
- The next mechanism chosen by where the measured gap is, with the exact command to run.
