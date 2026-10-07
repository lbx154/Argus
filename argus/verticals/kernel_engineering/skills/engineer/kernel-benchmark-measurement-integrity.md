---
name: "Kernel Engineering: Roofline First, Measure in Isolation"
description: "The single kernel-engineering skill. Reason from the physical limit (arithmetic intensity, ridge point, speed-of-light time) to the lever that can move a kernel, retrieve what you do not know from the right source, write the mechanism rather than sweep parameters, and measure so that the number is real: concurrent evaluation on shared hardware inflates latency 3-5x and turns the optimization loop into noise. One clean isolated run screens an idea; only isolated measurement under the official conditions certifies a win. Also the review stance: judge code, correctness and benchmark evidence, never process documents."
---

# Kernel engineering: roofline first, measure in isolation

This skill covers the whole of a kernel or inference-performance task: how to
decide what to change, where to learn what you do not know, how to measure so
the number means something, and how a Reviewer judges the result. It places no
bar on exploration. A bounded research mission that ends in a report is a valid
outcome, code is optional for it, and one clean run is enough to screen an idea.
What it does insist on is that a speedup you *claim* was measured in isolation
under the official conditions, because every other number in this document
turns out to be an illusion under load.

## How the work proceeds

Read the repository and current primary sources as broadly and deeply as the
question deserves, without framework paperwork: do not create scope documents,
algorithm plans, frontier ledgers, environment reports, baseline protocols,
outcome schemas, validation matrices, results reports or repeated checkpoints
unless the operator asks for one or a later task genuinely needs a concise
result. The kernel, the measurement and a short account of what was learned are
the evidence.

Reuse a settled baseline when one exists; exploration does not need to rerun
it. Explore several high-upside mechanism families, including radical and
uncertain ones. Do not prefer the smallest patch, the cheapest test,
immediate verifiability or immediate reproducibility when choosing what to
try: prefer expected upside and information gain over low risk, since a kernel
already near its physical limit gains nothing from another safe tweak. A failed
implementation is useful evidence about one family and closes nothing about
the others.

A bounded research mission may stop with a concise report that separates
sourced facts, reasoned hypotheses, open questions and promising mechanisms.
When the task is implementation, choose one coherent mechanism, preserve the
public API where required, and screen it with one clean run. Do not default to
multiple seeds, repeated controls or confidence campaigns; run broader
correctness checks and comparable target-hardware repetitions only for a win
you are about to claim or retain (see "Screening versus certifying" below).

## Step 0: find the physical limit

Never tune blindly. Every change is justified by a measured bottleneck and a
physical model of why the change should move the number. Parameter sweeps
without a model are noise; the win comes from changing the mechanism (memory
layout, what crosses DRAM, how work maps to SMs), not the block size.

A kernel is bounded by one of memory bandwidth, compute throughput, latency, or
launch overhead. Decide which with arithmetic before touching code:

- **Arithmetic intensity** `AI = FLOPs / bytes_moved`, FLOP per byte of DRAM
  traffic.
- **Ridge point** `AI* = peak_FLOPs / peak_BW`. Below it you are memory-bound
  and the only lever is moving fewer bytes or reusing them better; above it you
  are compute-bound and the lever is more efficient math (tensor cores, fewer
  instructions, better ILP).
- **Speed-of-light time** `t_sol = max(FLOPs / peak_FLOPs, bytes / peak_BW)`.
  A kernel already near `t_sol` is at the wall; only a different algorithm,
  with fewer bytes or fewer FLOPs, helps.

The peaks are properties of the card in front of you, so look them up for that
card rather than remembering them from another one. As an example of the
arithmetic: a B200 has HBM bandwidth around 8 TB/s and dense bf16 peak around
1.8 PFLOP/s, so its ridge sits near 226 FLOP/byte, and most elementwise, norm,
activation and attention-projection kernels sit far below any such ridge; they
are memory-bound, which means the work is in bytes moved, not FLOPs. An
elementwise op on an `N`-element bf16 tensor reads and writes `4N` bytes (in
and out; twice that if it also reads a weight), so `t_sol ≈ 4N / peak_BW`. If
the measured time is five times that, the kernel is wasting fourfold DRAM
traffic: look for redundant reads, un-fused passes, or loads narrower than 128
bits.

## The levers, by bottleneck

Memory-bound, the common case:

- Fuse passes so an intermediate never round-trips through DRAM. This is the
  single biggest win; each eliminated pass removes a full read and write.
- Vectorize global access to 128 bits (`float4`, `uint4`, bf16×8). Sub-word
  loads waste bandwidth and transactions.
- Coalesce: consecutive threads touch consecutive addresses. Fix strided or
  transposed access with shared-memory staging or a better tile.
- Reuse through shared memory and registers to cut redundant global reads;
  the tiled-GEMM idea generalizes.
- Pack (bf16×4 in a `uint64`) to move more useful payload per transaction.

Compute-bound:

- Use tensor cores (wmma, CUTLASS, cuBLASLt, cuDNN). Hand FMA loops lose by an
  order of magnitude on matmul, convolution and attention.
- Raise instruction efficiency and ILP, cut redundant work, use fast-math where
  the official tolerance allows it.

Latency- or occupancy-bound, typically small or serial kernels:

- Occupancy is active warps over the SM's maximum; too few warps and stalls are
  not hidden. It is a means, not a goal: past the point where latency is
  hidden, more occupancy can hurt through register and shared-memory pressure.
  Check registers per thread and shared memory per block against the SM budget.
- Launch overhead dominates tiny kernels: fuse, or use CUDA graphs or a
  persistent kernel.
- Tail effect: a grid that is not a multiple of the SM count leaves SMs idle at
  the end; pick grids that divide the machine.

## Retrieve what you do not know

The reusable facts live here: the roofline, the levers, how to measure.
Everything else you go and get; a strong optimizer is a fast learner, not an
omniscient one.

1. Read the definition and measure first. The task definition gives exact
   shapes and dtypes, so compute `AI`, `t_sol` and the byte budget yourself,
   and take the measured time from the harness. The gap between them is the
   bottleneck, stated precisely, and that is the research question you search
   with.
2. Profile when timing is not enough. `ncu` and `nsys` give achieved
   bandwidth, occupancy, stall reasons and L2 hit rate. If hardware counters
   are locked (`ERR_NVGPUCTRPERM`), derive achieved bandwidth as
   `bytes_moved / t` and compare it with the card's peak; that alone tells you
   the memory-bound headroom.
3. Search the right source, not just the web. For a mechanism or library:
   CUTLASS (GEMM, convolution, epilogue fusion), the cuDNN frontend (attention,
   convolution), cuBLASLt, the Triton documentation and tutorials, the CuTe
   and cuTile DSL examples (each official repository ships `examples/`). For
   the concept and the why: the CUDA C++ Programming Guide and Best Practices
   Guide, GPU MODE lectures, and the paper behind the kernel (the model
   identifier in the definition usually points at it). For the state of the
   art: search the operation together with the target architecture,
   FlashAttention and FlashInfer for attention, recent arXiv for fused
   variants.
4. Transfer from adjacent fields. Most kernel wins re-apply a few cross-domain
   ideas: blocking and tiling (cache-oblivious algorithms), operational
   intensity (HPC roofline), streaming and online algorithms (Welford for
   variance, online softmax for attention: compute the statistic in one pass
   instead of two), mixed precision and error analysis (when does fp32
   accumulation matter for the tolerance?), special-function identities
   (`ndtri`, `erf`: a statistics identity can replace an iterative solve).
   When stuck, ask which field already solved a bandwidth, precision or
   parallelism problem shaped like this one.

## Write the kernel; do not merely tune it

External knobs (block size, warps, stages) are the last few percent. The win
is structural and lives in the kernel body: what crosses DRAM (fuse, recompute
rather than store, keep it in registers or shared memory); the work
decomposition (row per block versus split-K, persistent grid, one-pass online
statistics); the numerics (bf16 storage with fp32 accumulation, exact versus
approximate special functions within tolerance); the instruction mix (tensor
cores, vectorized and packed I/O). When a sweep plateaus, that is the signal to
re-derive the bottleneck and rewrite the mechanism, not to sweep harder.

## Measure so the number is real

### Confirm the shape before allocating

A benchmark row's name is part of the evidence. Before a long compile or
allocation, verify that a name such as `L32_B1_T8K_D2K` matches the actual
tensor arguments and inspect the minimum input footprint:

```bash
python -m argus.verticals.kernel_engineering.benchmark_preflight \
  --shape-id L32_B1_T8K_D2K --L 64 --B 1 --T 8192 --D 8192 \
  --dtype bf16 --gpu-memory-gib 192
```

Exit code 2 means the label and the tensors disagree. Fix or rename the shape
before collecting evidence; never compensate for a mislabeled oversized row by
raising its timeout. If the footprint warning fires, first run one isolated row
with zero warmups and one repeat, record the allocation, compile and run phases
separately, then choose a bounded measurement plan.

### The trap, as it actually happened

A 24-kernel fleet was optimized in parallel, each kernel's engineer running its
own official-docker evaluation with locked clocks, cold L2, and every flag
right. The reported results looked spectacular: a MoE kernel "improved
28.67 ms → 5.49 ms, 5.22x".

Then a check against the official leaderboard: that kernel's reference is
6.01 ms. Our baseline had measured 28.67 ms, 4.8x slower than the official
reference for the same code. The evaluation was not wrong (`clocks_locked=True
official=true`); the conditions were. Measured in isolation, with the fleet
paused:

```
012 reference, isolated        : 5.62 ms   (≈ official 6.01 ms)
012 reference, 8-way load      : 28.67 ms  (5.1x inflated)
012 "optimized best", isolated : 5.53 ms   → the "5.22x" was 1.02x. Nothing.
```

The teammates had been optimizing against noise. Candidate and floor
evaluations ran under different, varying contention, so "this change improved
the candidate time" was a coin flip. This is worse than a wrong absolute number:
it makes the optimization loop chase ghosts.

### Where the wall-clock comes from

The official scorer reports the end-to-end wall-clock of `run()`. Decompose it:

- GPU compute is fixed by `--lock-gpu-clocks`; concurrency on other GPUs does
  not change it.
- CPU-side work in `run()`: launch overhead, and for many reference
  implementations a lot of real CPU computation (MoE token routing and
  gather-scatter, dynamic shapes, Python glue). It is multi-threaded through
  torch and OpenMP and scales with the cores available.
- The shared resource is therefore the CPU, not the GPU. Each concurrent
  evaluation spins up a thread pool sized to all cores; eight of them on a
  112-core pod demand about 896 threads, an eightfold oversubscription that
  thrashes, and the CPU-side balloons.

So the inflation concentrates in CPU-heavy kernels. A GPU-bound attention kernel
barely moved under eight-way load (0.81 → 0.89 ms); the CPU-heavy MoE blew up
fivefold. Know which one you have: is `run()` doing real CPU work, or just
launching a kernel?

### Why the obvious fixes do not work

1. Pin each evaluation to its own cores (112/8 = 14 each). Tested: the MoE
   reference needs nearly all cores to reach its official latency, and confined
   to 14 it measured 20 ms, still 3.6x off. You cannot give eight concurrent
   evaluations "enough" cores when one wants the whole machine; pinning trades
   oversubscription for starvation, and neither matches the isolated number.
2. Average it out with more trials. Contention is not zero-mean noise; it is a
   systematic upward bias that varies with how many neighbours happen to be
   evaluating. No amount of averaging recovers the isolated number.
3. A per-GPU lock so that only one evaluation runs per card. Still up to eight
   across eight cards, still one oversubscribed CPU. The lock has to be global.

### The only fix that is comparable to the official number

Isolated serial measurement: a single global lock so exactly one evaluation
runs pod-wide at a time, with all cores available. That reproduces the official
isolated-container protocol, every number is then comparable to the
leaderboard, and the loop's relative comparisons are valid.

```bash
exec 9>/tmp/eval-GLOBAL.lock      # one lock for the whole pod, not per GPU
flock -w 1800 9 || { echo "EVAL_LOCK_TIMEOUT"; exit 3; }
# ... run the official scorer here, with all cores ...
```

The cost is real: evaluation is now serial and becomes the throughput
bottleneck. Size the optimizer pool to what serial evaluation can feed, a small
number in flight rotating over a large backlog, rather than a huge pool all
measuring at once. Buy throughput back by adding isolated evaluation capacity
(more pods or machines), never by sharing one.

### The discipline

1. Always have an isolation baseline. Measure the reference once with nothing
   else running and compare it with the official leaderboard reference. If they
   do not agree (allow roughly 15% for a different physical card), your
   conditions are wrong; stop and fix them before trusting any number.
2. A speedup measured under load is not a result. Before reporting, re-measure
   the winner in isolation. If it does not hold there, it was contention.
3. Anchor the speedup to the official reference, not to your own first
   measurement. A continued run's "baseline" may already be a possibly inflated
   optimized version; the honest denominator is the official reference.
4. Suspect the measurement when a win is implausibly large: 5x on a kernel the
   leaderboard's best only improves 2.25x is almost always broken measurement,
   not genius.
5. Concurrent measurement on shared hardware produces fabricated numbers even
   when every flag says `official=true`. The flags certify the scorer, not the
   isolation.

## Screening versus certifying

"One clean run is enough" and "the official scorer with cold L2 and locked
clocks is the only truth" are both right, about different questions. One clean
isolated run screens an idea: it tells you whether a mechanism family is worth
pursuing, and asking for more at that point taxes exploration for no
information. A win that will be claimed, retained, or compared with the
leaderboard is measured under the official conditions, cold L2, locked clocks,
cloned inputs and the global lock, because the harness flushes L2, clones
inputs and rejects timing tricks, and a "speedup" from a warm cache, a hidden
stream, fewer real bytes than the specification, or a degenerate output is not
a speedup. Neither question asks for repetition by default: a screen and a
certification alike do not require multiple seeds or repeated trials until a
promising candidate is about to be certified and the isolated numbers are
close enough that noise could decide.

## Reviewing a kernel change

Review the implemented change and the evidence that decides it, and do so
without requiring process documents:

- the intended code path is actually exercised;
- the public API and any fallback remain correct;
- tests cover the relevant shapes, dtypes and failure boundaries;
- baseline and candidate were measured under comparable conditions, and a
  retained win in isolation under the official conditions;
- latency, memory and other claimed benefits exceed noise without hidden
  regressions.

A report-only research mission is judged on primary-source quality, factual
accuracy, synthesis and usefulness to the next engineering decision; code is
not required. Speculative, radical, unimplemented or not-yet-reproducible ideas
remain valid exploration when they are labelled as hypotheses rather than
claimed results. One clean exploratory screen is sufficient for a screen; do
not request multiple seeds, repeated controls or confidence campaigns unless a
promising candidate is being claimed or retained. Request a repair only for a
concrete code or evidence defect, and never block completion because a
framework-specific scope, frontier, environment, baseline, outcome, validation,
report or checkpoint file is absent.
