---
name: SOL Kernel Hands-on Trace
description: A concrete KernelBench/SOL process trace from optimizing 36_RMSNorm_ on B200, recorded as the measured causal chain we require — per iteration measured(ms, effective_BW, %peak, SOL) → bottleneck hypothesis → mechanism → re-measured → gap-to-SOL → next — with the roofline arithmetic shown, plus the real infra/correctness nails that interrupted the chain.
category: benchmark-kernel-optimization
version: 2
scientist_model: gpt-5.5
created_at: 2026-06-17T00:00:00+00:00
---

## Title
SOL Kernel Hands-on Trace

## Description
A **human-written seed exemplar** of how to optimize a KernelBench/SOL-style kernel
— a real, measured process trace to learn from and to keep evolving, not a polished
abstract playbook and not something harvested from a live run (the agent's own run
may take a wrong path; the seed is deliberately a known-good one). The trace's spine
is the reasoning we want the agent to internalize: a per-iteration **measured causal
chain**, where each step is bound to a real number and the next hypothesis follows
from the *gap to the roofline*, not from vibes. Infra and correctness failures are
recorded too, but as **interruptions to the chain**, not as the point of the trace.
Treat this as a starting exemplar: when a real kernel teaches you a sharper chain,
improve it.

## When to use
- The task is a correctness-gated GPU kernel benchmark with a frozen scorer.
- The agent must produce real speed/SOL numbers on B200/H100/A100 and record the
  measure → hypothesize → re-measure loop, including roofline arithmetic.
- The user asks for a trace, postmortem, or "what actually goes wrong when you try
  this yourself".
- A benchmark vertical task is stuck because the agent jumped to a mechanism
  without first measuring *which wall it is hitting*.

## When NOT to use
- You only need the high-level SOL workflow and artifact contract. Use
  `SOL Kernel SOTA Optimization`.
- The task is a paper benchmark matrix or model-training run rather than one
  kernel implementation.
- The scorer is unavailable and the user explicitly asks you not to attempt
  recovery.

## The one rule this trace exists to teach

> **The decisive insight is measured, not known.** You do not "know" a kernel is
> memory-bound; you measure its effective bandwidth against the roofline and read
> the bottleneck off the gap. Every link in the chain below carries a real number
> from the frozen scorer or from arithmetic on a real measured time. A link with
> an invented bandwidth is not a data point — it is fabrication, and it scores 0
> the same as a wrong kernel.

## What "process data" means here — the chain to learn

This exemplar records each optimization iteration as one linked block. Learn this
shape and produce it when it helps; it is a teaching exemplar, not a rigid form a
harness will grep for:

```text
ITER <n> — <what this candidate is>
  measured:    cand_ms=<frozen scorer's graded time>, SOL=<frozen scorer>
               effective_BW = <SOL-minimal bytes> / cand_ms = <X> TB/s = <P>% of <peak> TB/s
  read:        <one line — what the % tells you, e.g. "21% → ~5x redundant HBM traffic">
  bottleneck:  <hypothesis derived from the numbers: memory-bound / compute-bound /
                launch-overhead / occupancy / layout — and WHY the numbers say so>
  mechanism:   <the single concrete change this iteration makes, and why it attacks
                that bottleneck>
  re-measured: cand_ms=<frozen scorer's graded time>, SOL=<frozen scorer>; effective_BW=<X'> TB/s = <P'>% peak
  gap-to-SOL:  <opt_ms / SOL=1.0 floor> ; we are at <P'>% of peak, <delta> headroom below
  → next:      <the next hypothesis, chosen by where the remaining gap is>
```

**You compute the roofline yourself — the harness does not feed it to you:**

- **Bytes moved (SOL-minimal):** `numel × dtype_bytes × (reads + writes)`, taken from
  the kernel's **fixed shape/dtype API contract** (what the scorer feeds), not from
  input statistics/sparsity. For a read-once/write-once elementwise/normalization
  kernel that is `2 × numel × dtype_bytes`. Use the *minimal* traffic the operation
  requires; the gap between that and the achieved time is the redundant traffic you
  can remove.
- **Effective bandwidth:** `bytes_moved / cand_ms`, where `cand_ms` is the **frozen
  scorer's** time for the graded candidate — never a `gpu_run.py` debug time (it can
  diverge in precision/shape and fabricate a plausible-but-wrong bandwidth).
- **Peak bandwidth (the denominator):** measure it — do not trust the spec sheet.
  Run a trivial copy/saxpy kernel on the actual card via `gpu_run.py` and use the
  achieved HBM bandwidth. *Cross-check* against the scorer's SOL floor (see below) as
  confirmation only, never as the sole source — picking a slow kernel's `opt_ms`
  silently lowers the peak and inflates every later `%peak`.
- **SOL score:** comes from the **frozen scorer only**. `%peak-BW` is your own
  diagnostic for *how much headroom remains* — it never feeds the graded SOL, so its
  only job is to point you at the next bottleneck honestly. They move together but
  are different scales — never report one as the other.

## Real trace: KernelBench 36_RMSNorm_ on B200

Run in `/home/argustest/kb-hands-on-trace`, copied from the real
`kernelbench-mission-b200` scaffold. Target: `solutions/36_RMSNorm_.py`. Official
scoring: `./eval_solution.sh solutions` (frozen B200 eval server at
`http://127.0.0.1:2232`). Debug runs: `python gpu_run.py <script.py>`.

Reference op — RMSNorm along `dim=1` for shape `(112, 64, 512, 512)`, fp32:

```python
rms = torch.sqrt(torch.mean(x ** 2, dim=1, keepdim=True) + eps)
return x / rms
```

### The roofline, computed once up front

This is the arithmetic the agent must do before choosing a mechanism:

```text
numel        = 112 * 64 * 512 * 512 = 1.879e9 elements
dtype        = fp32 (4 bytes)               # the kernel loads `float`
SOL-minimal  = 2 * numel * 4 = 1.503e10 bytes = 15.03 GB   # read x once, write y once
```

Consistency check that pins the peak (do this — it is the turn-1 insight in action):

```text
scorer opt_ms (SOL=1.0 time) = 1.879 ms
15.03 GB / 1.879 ms = 8.00 TB/s
```

The scorer's SOL=1.0 time corresponds to **exactly 8.0 TB/s over the 2-pass byte
count** → the SOL target *is* the bandwidth roofline, ~8 TB/s is the right
denominator, and fp32 is confirmed (bf16 would have given a non-clean 4 TB/s).
RMSNorm is therefore a **memory-bound** kernel whose ceiling is "read x once,
write y once at full HBM bandwidth".

This closure happened to be exact; do not expect that. A cross-check landing within
~±5% of a copy-kernel-measured peak is normal (clock scaling, scorer quantization,
ECC) — defend the **measured** number, not a forced-clean one, and never nudge a
byte count or a peak to make the arithmetic close.

### The measured chain

```text
ITER 0 — baseline eager (`class ModelNew(Model): pass`)
  measured:    cand_ms=8.898, SOL=0.498
               effective_BW = 15.03 GB / 8.898 ms = 1.69 TB/s = 21% of 8.0 TB/s
  read:        21% of the roofline → ~5x the SOL-minimal bytes are being moved.
  bottleneck:  memory-bound, and eager pays it ~5 times: x**2, mean over dim=1,
               sqrt/rsqrt, divide each make a full-tensor HBM round-trip plus
               intermediates. The win is REMOVING redundant round-trips, not
               faster math.
  mechanism:   fuse all passes into one CUDA kernel — one thread owns one
               (batch, spatial) position, loops the 64 channels in registers,
               computes rsqrt(sum_sq/64 + eps), writes 64 normalized values.
               Read x once, write y once → approach the 8N-byte floor.
  re-measured: cand_ms=2.258, SOL=0.893
               effective_BW = 15.03 GB / 2.258 ms = 6.66 TB/s = 83% of 8.0 TB/s
  gap-to-SOL:  at 83% of peak; SOL floor opt_ms=1.879 ms (8.0 TB/s) is 0.38 ms /
               17% of peak BW away (headroom — we are below the roofline, not above)
  → next:      the remaining 17% is launch/occupancy or the 64-stride (uncoalesced)
               channel writes. Next hypothesis: warp-per-position cooperative
               reduction, or vectorized (float4) channel access for coalescing.
```

That single block — measured 21%, read "5x redundant", fuse, re-measured 83%, gap
17%, next hypothesis pointed at the remaining gap — **is** the process datum. The
old version of this trace recorded only ITER 0's `cand_ms` and ITER 1's final
`cand_ms`; the bandwidth column, the "why 21%", and the next-hypothesis-from-the-gap
were missing. Those are the parts that transfer to the next kernel; do not skip them.

### Implementation that realized ITER 1

```python
from torch.utils.cpp_extension import load_inline

class ModelNew(Model):
    def forward(self, x):
        return _get_rmsnorm_ext().rmsnorm_b200_forward(x, float(self.eps))
```

```c
const long base = b * 64L * spatial + s;
float vals[64];
float sum_sq = 0.0f;

#pragma unroll
for (int c = 0; c < 64; ++c) {
    const float v = x[base + (long)c * spatial];
    vals[c] = v;
    sum_sq += v * v;
}

const float inv_rms = rsqrtf(sum_sq * 0.015625f + eps);   // 1/64 = 0.015625

#pragma unroll
for (int c = 0; c < 64; ++c) {
    y[base + (long)c * spatial] = vals[c] * inv_rms;
}
```

Self-debug correctness on B200 before timing:

```text
shape (112, 64, 512, 512)
max_abs_err 8.344650268554688e-07
allclose_1e-2 True
```

Final official scorer:

```text
36_RMSNorm_ Y 2.258ms 3.90x SOL=0.893 tc_SOL=0.619 opt_ms=1.879 BEATS tc ✓
RESULT mean_SOL=0.6026 correct=8/8 beats_torch.compile=5/8
[scorer] NEW GLOBAL BEST mean_SOL=0.6026 (prev 0.5536)
```

## The nails that interrupted the chain

The chain above is the clean story. In the real run, four infra/correctness nails
came first — each one would have produced a *fake* chain link if not caught. Record
them too, because "the first failure is usually infrastructure, not algorithm."

- **Nail 1 — scorer bridge down.** `ERROR: B200 eval server unreachable at
  http://127.0.0.1:2232 ([Errno 111] Connection refused)`. Do not infer a score
  when the scorer is down — there is no measured link without it. Use
  `set -o pipefail` so a piped scorer failure cannot look successful. Recovery:
  `kubectl port-forward pod/argus-kbench-evalsrv 2232:9000 --address 127.0.0.1`
  then `curl -fsS http://127.0.0.1:2232/health`.
- **Nail 2 — baseline file was not a valid candidate.** Scorer said
  `candidate defines no ModelNew`. `baseline/*.py` has `Model`; `solutions/*.py`
  must define `ModelNew`. A "score" that is really a no-ModelNew error is a
  fabricated link. Fix: `class ModelNew(Model): pass`.
- **Nail 3 — fresh sandbox missed `results/`.** Scorer printed a `RESULT` line then
  crashed appending history: `FileNotFoundError: 'results/sol_history.csv'`. A
  printed score with a non-zero exit is still a failed run. Record both the visible
  line and the exit code. Fix: `mkdir -p results`.
- **Nail 4 — wrong reduction axis + debug transport.** First code attempt used
  `dim=-1` (wrong) and tried to load the candidate file remotely, which failed:
  `gpu_run.py` sends only the script body to `/run` and does **not** sync local
  files. Embedded-source debug then exposed the math bug:
  `max_abs_err 0.827 ... allclose_1e-2 False`. A wrong-axis kernel can look
  plausible from code structure and is numerically invalid → SOL 0. Always print
  `max_abs_err` / `allclose` before any timing link goes into the chain.

## How to apply this chain to another benchmark kernel

1. Run the official scorer once with `set -o pipefail`. If infra fails, fix or
   report infra; do not optimize, and do not write a chain link without a real score.
2. Check the candidate API (`ModelNew`, unchanged `Model/get_inputs/get_init_inputs`)
   and create missing artifact dirs (`results/`, `attempts/`) before long runs.
3. Compute the roofline up front: SOL-minimal bytes, and a *defended* peak BW
   (measure it on the card or cross-check against the scorer's opt_ms floor).
4. Write one deliberately small correctness probe (`max_abs_err`, `allclose`)
   before timing.
5. For each iteration, record the full chain block: measured(ms, effective_BW,
   %peak, SOL) → read → bottleneck → mechanism → re-measured → gap-to-SOL → next.
6. Choose the next mechanism from **where the remaining gap is**, not from a menu
   of generic tricks. 21% of roofline → remove redundant traffic (fuse). 83% of
   roofline → chase coalescing/occupancy/launch. ~compute-bound → feed the tensor
   cores.
7. Stop only when the gap to SOL is small or the next mechanism's expected gain is
   below noise — and say which it is.

## Response shape
- State the exact scorer command and whether it exited 0.
- Show the roofline arithmetic (SOL-minimal bytes, peak BW, how you defended peak).
- Quote every infra/correctness nail before any timing link.
- For each iteration, give the full measured chain block with real numbers.
- Quote the official per-kernel line and `RESULT` line for every re-measured link.
- End with the next hypothesis chosen by the remaining gap to SOL.
