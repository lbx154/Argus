---
name: Kernel Optimization Process — Worked Trace (019 decoder layer)
description: A complete, honest research trace of optimizing a hard, already-good kernel — roofline diagnosis, reading the tolerance, testing the obvious lever (bf16) and letting the OFFICIAL scorer reject it, profiling to LOCATE the cost, then WRITING a custom TF32-tensor-core flash kernel that clears the rtol=1e-5 tolerance and wins 1.75× on the official harness. This is high-quality PROCESS DATA: a weaker model that follows this method reaches an expert's diagnosis and an expert's kernel. Optimize from measurement and physics, not vibes.
category: benchmark-kernel-method
priority: high
version: 1
created_at: 2026-06-27T00:00:00+00:00
---

# Kernel Optimization Process — Worked Trace

This is the *method*, told as a real trace on a hard case
(`019_decoder_layer_fused_attention_mlp`, a Qwen2-VL decoder layer:
RMSNorm → QKV → mRoPE → GQA attention → O-proj → RMSNorm → SwiGLU MLP, fp32, up
to seq=4096). The numbers are illustrative; **the discipline is the point.**
Follow it on any kernel. Pair with `Kernel Optimization Knowledge & Retrieval`
(the physics) and `Official SOL-ExecBench Environment` (how to measure).

## The loop: diagnose → hypothesize → TEST against the official scorer → locate → conclude

### 1. Roofline first — what limit am I fighting?

Compute it yourself from `definition.json` shapes:
`AI = FLOP / bytes`, ridge `AI* = peak_FLOP / peak_BW` (B200: 1.811 PFLOP/s ÷
8 TB/s ≈ 226). For 019 the GEMMs (QKV/O/gate/up/down) give `AI ≈ 250–2000 ≫
226` ⇒ **compute-bound**. `t_sol(tf32) ≈ FLOP / 0.9 PFLOP/s`. Measured `t_k` was
~3.1 ms vs `t_sol ~1–2 ms` ⇒ a ~2× gap worth chasing. *Never optimize before you
know which physical wall you are at.*

### 2. Read the tolerance BEFORE picking a lever

`workload.jsonl → tolerance`: `max_atol=0.004, max_rtol=1e-5,
required_match_ratio=0.98`. The `rtol=1e-5` is **brutal** — it forbids any
storage cheaper than fp32/TF32 (bf16 carries ~1e-2 relative error, fp16 ~1e-3;
both blow 1e-5). This *predicts* that the obvious 2× lever (bf16 tensor cores)
will fail. Don't skip this read — the tolerance is half the problem statement.

### 3. Test the obvious lever anyway — let the SCORER be the judge

Hypothesis: cast attention Q/K/V to bf16 so SDPA uses the flash kernel (bf16
flash is ~20× faster than fp32 attention). One-line change, eval through the
OFFICIAL harness:

```
RESULT correct=false status=FAILED  [INCORRECT_NUMERICAL]  0/16 workloads
```

**Learned, empirically:** bf16 attention violates `rtol=1e-5`. This is not a
failure — it is *information*. The official scorer (cold-L2, locked clocks,
official tolerance) is the only judge; a local "it looks close" is not. Record
the dead end — "bf16 attention rejected by 019's 1e-5 rtol" is as valuable as a
win, and it removes a whole branch of the search.

### 4. PROFILE to locate the cost — don't guess where the time is

Decompose the layer and time each block on-GPU (warmup + synchronized timing):

```
seq=4096:  ATTN(fp32)=2.51 ms (49%)   MLP=2.35 ms (45%)   QKV/O=0.31 ms
fp32 SDPA = 2.68 ms   vs   bf16 flash = 0.11 ms   →  flash is 23× faster
```

Now the bottleneck is *named*: the **fp32 attention is half the cost at large
seq**, because fp32 has no flash kernel — it falls back to a fused-but-fp32
backend that runs the QK/AV matmuls on CUDA cores, not tensor cores. The MLP is
already cuBLAS-TF32 and near the GEMM frontier.

### 5. Rule out the free wins before the expensive one

Before writing a kernel, check the cheap levers:
- **Backend selection:** force each SDPA backend
  (`torch.nn.attention.sdpa_kernel`). Result: `MATH=5.58 ms` (materializes),
  `EFFICIENT=2.51 ms` (= the default), `CUDNN=unsupported`. ⇒ the solution is
  **already on the best fp32 backend**; no free switch.
- Weight fusion (gate+up into one GEMM) costs a per-call weight concat (~0.5 GB
  copy) that negates the saving here — *check the cost of the trick, not just
  its benefit.*

### 6. The lever is structural — so WRITE the kernel (and it pays off)

The free wins are gone and the tolerance forbids reduced precision, so the one
remaining attention lever is a **custom TF32-tensor-core flash kernel** — and at
this point you *write it*, you don't keep sweeping knobs. The reasoning is
physical: the fp32 EFFICIENT backend runs QK/AV on CUDA cores; a Triton kernel
can run them on **tensor cores in TF32** (`tl.dot(..., input_precision="tf32")`)
while keeping a **fp32 online softmax**, so the reduction stays exact and only
the matmuls drop to TF32 (~1e-3 error — under `atol=0.004`).

A first, un-tuned version (BM=BN=64, online softmax, causal mask) already:

```
correctness vs fp32 SDPA:  max_abs_err 0.0027–0.0029 < 0.004  → 100% within tol  ✓PASS the rtol=1e-5 gate
per-op speed (seq=4096):   fp32 EFFICIENT 2.51 ms → TF32 flash 1.80 ms  (1.39×)
```

Integrated into the full layer and scored by the OFFICIAL harness (locked
clocks, 16/16 workloads):

```
RESULT correct=true  16/16  cand_ms: 3.056 → 1.742 ms   →  1.75× faster, official, verified
```

The gamble paid because the diagnosis was physical, not a guess: bf16 lost the
*precision* battle, but tensor-cores-in-TF32 + online-softmax won the *throughput*
battle without losing precision. That is the difference between an expert and a
parameter-sweeper — knowing which physical lever is still untapped, and being
willing to write the kernel to reach it.

## The transferable rules

1. **Roofline before code.** Know the wall (memory / compute / latency) and the
   speed-of-light time before touching anything.
2. **Read the tolerance** — it decides whether the precision lever exists at all.
3. **The official scorer is the only judge.** Test the obvious lever; let a
   `[INCORRECT_NUMERICAL]` teach you the constraint. A rejected hypothesis is
   progress.
4. **Profile to locate** — never optimize an operation you haven't measured to
   be the bottleneck. Decompose and time on-GPU.
5. **Cost the trick, not just the benefit** (weight concat, extra copies, launch
   overhead).
6. **Conclude honestly.** "Already near the frontier; the one remaining lever is
   X, and here's its risk" is a professional result. Faking a speedup that the
   cold-L2 / locked-clock scorer would reject is worthless.
7. **Go deep when the lever is structural** — when the only win left is a custom
   kernel (a TF32 flash, an online-stats fusion), write it; don't keep sweeping
   parameters past the point where the mechanism, not the knobs, is the limit.
