---
name: Kernel Benchmark Measurement Integrity — Isolation Is the Whole Game
description: A hard-won, worked trace on why a "speedup" can be a total illusion — concurrent evals on shared hardware inflate the measured latency 3-5x and corrupt the optimization signal. How to think about WHERE the measured time comes from (GPU clocks locked, CPU shared), why per-GPU isolation isn't enough for CPU-heavy kernels, and the only fix that makes numbers official-comparable: isolated serial measurement. Never report a speedup measured under load.
category: benchmark-kernel-method
priority: high
protected: true
version: 1
created_at: 2026-06-28T00:00:00+00:00
---

# Kernel Benchmark Measurement Integrity

**The single most expensive lesson:** a kernel's measured latency is only meaningful
if it was measured in **isolation**. Measure it while other work shares the hardware
and the number inflates — silently, by **3-5x** — and every "speedup" computed against
it is an artifact. You will think you found a 5x win when you found nothing. Pair with
`Official SOL-ExecBench Environment` (how to measure) and `Kernel Optimization
Knowledge & Retrieval` (roofline of the kernel; this skill is the roofline of the
*measurement*).

## The trap, as it actually happened

A 24-kernel fleet optimized in parallel, each kernel's engineer running its own
official-docker eval (locked clocks, cold-L2, 16/16 — all the right flags). Reported
results looked spectacular: a MoE kernel "improved 28.67 ms → 5.49 ms = 5.22x".

Then a check against the official leaderboard: that kernel's **reference** is 6.01 ms.
Our *baseline* measured 28.67 ms — **4.8x slower than the official reference of the
same code.** The eval flags were all correct (`clocks_locked=True official=true`), so
the eval wasn't wrong — the *conditions* were. Measured in isolation (fleet paused):

```
012 reference, isolated  : 5.62 ms   (≈ official 6.01 ms ✓)
012 reference, 8-way load : 28.67 ms  (5.1x inflated)
012 "optimized best", isolated : 5.53 ms   → the "5.22x" was really 1.02x. Nothing.
```

The teammates had been optimizing against **noise**: their candidate evals and floor
evals ran under *different, varying* contention, so "this change improved cand_ms" was
a coin flip. The whole signal was corrupted. This is worse than a wrong absolute
number — it makes the optimization loop chase ghosts.

## Roofline of the *measurement*: where does the wall-clock come from?

The official scorer reports end-to-end `run()` wall-clock. Decompose it:

- **GPU compute** — fixed by `--lock-gpu-clocks`. Concurrency on *other* GPUs does not
  change it. This part is safe.
- **CPU-side work in `run()`** — kernel launch overhead, and for many reference impls a
  lot of *real* CPU computation (MoE token routing/gather-scatter, dynamic shapes,
  python glue). This is multi-threaded (torch/OMP) and **scales with available cores**.
- **The shared resource is the CPU, not the GPU.** N concurrent evals each spin up a
  torch threadpool sized to *all* cores. On a 112-core pod, 8 concurrent evals demand
  ~896 threads on 112 cores → **8x oversubscription → thrash → the CPU-side balloons.**

So the inflation is concentrated in CPU-heavy kernels. A GPU-bound attention kernel
barely moved (0.81 → 0.89 ms under 8-way load); the CPU-heavy MoE blew up 5x. **Know
which one you have** (is `run()` doing real CPU work, or just launching a GPU kernel?).

## Why the obvious fixes don't work

1. **"Just pin each eval to its own cores" (cpuset 112/8 = 14 cores each).** Tested: the
   MoE reference *needs* ~all cores to hit its official latency, so confined to 14 it
   measured **20 ms** — still 3.6x off. You cannot give 8 concurrent evals "enough"
   cores when a single one wants the whole machine. Pinning trades oversubscription for
   starvation; neither matches the isolated official number.
2. **"Average it out / more trials."** Contention is not zero-mean noise — it is a
   systematic upward bias that varies with how many neighbors happen to be eval'ing.
   No amount of averaging recovers the isolated number.
3. **"Per-GPU lock so only one eval per card."** Still up to 8 across 8 cards → still
   oversubscribes the shared CPU. The lock has to be **global**, not per-GPU.

## The only fix that is official-comparable

**Isolated serial measurement: a single global lock so exactly one eval runs pod-wide
at a time, with all cores available.** That reproduces the official "isolated
container" protocol. Every number is then comparable to the leaderboard, and the
optimization loop's relative comparisons are valid.

```bash
exec 9>/tmp/eval-GLOBAL.lock      # ONE lock for the whole pod, not per-GPU
flock -w 1800 9 || { echo "EVAL_LOCK_TIMEOUT"; exit 3; }
# ... run the official scorer here, with all cores ...
```

The cost is real: eval is now serial, so it is a throughput bottleneck. **The fix is to
size the optimizer pool to what serial eval can feed** — a small pool (e.g. 8 in-flight)
rotating over a large backlog, NOT a huge pool all measuring at once. Throughput you
buy back by adding *isolated* eval capacity (more pods / machines), never by sharing one.

## How to not get fooled again (the discipline)

1. **Always have an isolation baseline.** Measure the reference once with nothing else
   running; compare to the official leaderboard reference. If they don't match (±~15%
   for a different physical card), your conditions are wrong — stop and fix them before
   trusting *any* number.
2. **A speedup measured under load is not a result.** Before reporting, re-measure the
   winner in isolation. If it doesn't hold isolated, it was contention.
3. **Anchor speedup to the official reference, not your own first measurement.** A
   re-formed/continued run's "baseline" may already be a (possibly inflated) optimized
   version; the honest denominator is the official reference.
4. **Suspect the measurement when a "win" is implausibly large** (5x on a kernel the
   leaderboard's best only gets 2.25x on). Implausible speedups are usually broken
   measurement, not genius.
5. **Honesty:** concurrent measurement on shared hardware = fabricated numbers, even if
   every flag says `official=true`. The flags certify the *scorer*, not the *isolation*.
   No fake wins.
