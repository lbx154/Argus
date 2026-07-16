---
name: Speedrun Hands-on Trace
description: A concrete NanoGPT-speedrun (Recursive "First Steps" Task 2) process trace on 8xH100, written as the operator's OWN known-good operating procedure — opening with a SEARCH-FIRST step (the agent is knowledge-limited: retrieve and reproduce the concrete prior art before building, general technique only, never the answer key), then the exact commands run, the real recipe knobs (line-cited) chosen by lever class, the real measured convergence curve and per-step profile, and the decision at each fork — recorded as a measured causal chain: measured(train_time, val_loss, p from the frozen t-test) → decompose(steps x per-step, convergence curve) → pick the binding lever → one change → re-measure → classify miss (quality vs statistical power) → bank N=10 / stack. The agent's own live run took the wrong path; that path is demoted to the nails.
category: benchmark-training-speedrun
version: 3
created_at: 2026-06-18T00:00:00+00:00
---

## Title
Speedrun Hands-on Trace

## Description
A **human-written seed exemplar** of how to optimize a training-speedrun benchmark
(minimize wall-clock to a fixed quality target under a statistical validity gate). It is
written deliberately as the **known-good operating procedure** — the concrete commands and
recipe knobs I would actually run, with the decision at each fork — *not* a transcript of
the agent's live run (that run nibbled a single mechanism family for hours and is recorded
below only as the **nails** — what not to do). The spine is a per-candidate **measured
causal chain** where every step is bound to a real number from the frozen scorer, every
knob is line-cited from the live recipe, and the next move follows from **the binding
constraint** — which on this task is *convergence at the tail*, and at certification time
is *statistical power*, not recipe quality. When a real run teaches a sharper chain, evolve
this.

## When to use
- The task is a wall-clock training-speedrun to a fixed metric target (e.g. modded-nanogpt
  `val_loss <= 3.28`) with a frozen scorer and a **statistical validity gate** (a t-test
  over N runs), on real GPUs (8xH100 / B200).
- The agent must produce real `train_time` / `val_loss` / `p` numbers and record the
  measure → decompose → pick-lever → re-measure → bank loop.
- A speedrun is stuck nibbling: many mechanisms tried, floor not moving, a quality-valid
  near-win sitting un-certified.

## When NOT to use
- You only need the high-level methodology and toolkit. Use `Speedrun SOTA Optimization`.
- The task is a single GPU kernel (use `SOL Kernel Hands-on Trace`), a paper matrix, or an
  RL/post-training run.
- The scorer is unavailable and the user explicitly asks you not to attempt recovery.

## The one rule this trace exists to teach

> **Attack the biggest lever, not the most fun one — and a faster number is not a result
> until the t-test certifies it.** On a near-SOTA training recipe the per-step kernels are
> already near the roofline; the leverage is in **fewer steps to the target** (convergence),
> which compounds. And when a candidate's mean is already inside the gate, the binding
> constraint is *sample size*, not your recipe — you certify (N=10), you do not re-engineer.
> The live run violated both halves: it spent hours on per-step FP8/MLP precision while the
> optimizer/schedule went untouched, and it kept building mechanisms on a recipe that was
> already valid at N=10. That cost ~3 hours and ~6 missions.
>
> **And a rule precedes even this: you are knowledge-limited, so RETRIEVE before you build.**
> Reciting a lever from memory (as the FIRST draft of this trace did — "add SGLD / leaner MLP")
> gets its coefficients and conditions wrong; you go read the concrete prior art and reproduce
> it. Invention here is *disciplined recombination of retrieved, reproduced knowledge*, not
> recall — see `Speedrun SOTA Optimization` §0. (Anti-cheat: general technique only, never this
> task's answer key.)

## What "process data" means here — the chain to learn

Record each candidate as one linked block. Learn the shape; it is a teaching exemplar, not
a rigid form a harness greps for:

```text
CAND <name, ON TOP OF which floor>
  command:     <the exact scorer / run command executed>
  measured:    train_time=<scorer mean>±sd over N=<n>; val_loss=<mean>±sd;
               p(mean<3.28)=<frozen analyze_sweep t-test>  -> VALID/INVALID
  decompose:   steps × per_step (instantaneous, by stage); convergence: val_loss@steps
               (where does the curve cross the target?)
  read:        faster/slower? quality in or out? if INVALID -> QUALITY (mean>target) or
               POWER (mean<<target, p just over)?
  lever:       which of {convergence(steps) | per-step-cost | precision-stability} this
               change attacks, named to a real knob
  decision:    bank N=10 | stack & co-tune | revert this knob | abandon line — by the rule
  → next:      next change, chosen by the binding constraint
```

## My run: the concrete operations

Project: `/home/argustest/nanogpt-speedrun-h100`. Scorer (frozen):
`./eval_solution.sh solution <N>` — ships `solution/{train.py,triton_kernels.py}` to 8xH100
(`ssh h100`, frozen interp `/scratch/nano/envs/sr210/bin/python`, torch 2.10+cu128 / triton
3.6 / FA3), runs `torchrun --nproc_per_node=8` N times, `analyze_sweep.py` runs the one-sided
t-test `t=(mean-3.28)/(sd/√n)`, prints `SCORE valid=<bool> n=<N> val_loss=<m>±sd
p(mean<3.28)=<p> train_time=<m>±sd s`. `valid` iff `p<0.01`. Iterate at N=3, certify at N=10.
Target `val_loss<=3.28`, minimize `train_time`. Anchors (8xH100): #83 official 79.7s; our #83
re-measure 80.18s; automated frontier (Recursive) 77.3s.

### OP 0 — SEARCH the literature, reproduce the anchor, extract the curve

Before any edit I retrieve human knowledge first — I am knowledge-limited and reciting levers
from memory gets them wrong (general technique ONLY; searching this task's leaderboard/answer
is disqualifying). The concrete searches, and what each is FOR:

```text
# the concrete artifact for each lever I might pull (arxiv/repo, not my memory of it):
WebSearch "Muon optimizer Newton-Schulz Moonlight RMS-match AdamW weight decay"
WebSearch "warmup-stable-decay WSD cooldown fraction" ; "muP muTransfer LR transfer small proxy"
WebSearch "RHO-loss reducible holdout online batch selection" ; "sequence length warmup curriculum"
WebSearch "FP8 training delayed scaling SwiGLU outlier instability Transformer Engine"
WebSearch "Fantastic Pretraining Optimizers equal tuning speedup at scale"   # treat every N× as an UPPER bound
# the diagnostic method, so I self-measure the bottleneck instead of guessing:
WebSearch "GPU roofline MFU 6N per token nsys torch.profiler per-step breakdown"
```

I keep the most CONCRETE form (coefficients, ablation conditions), corroborate any "N× faster"
against a second source, and treat it as a hypothesis to reproduce — not a fact. THEN reproduce
the anchor on our own box and read the real curve:

```bash
cd /home/argustest/nanogpt-speedrun-h100
./eval_solution.sh solution 3                                   # reproduce the #83 seed on OUR box
grep -E 'step:[0-9]+/1385 val_loss:' experiments/<RUNID>/run_1.txt   # extract the curve
```

```text
CAND seed #83 — baseline (no edits)
  measured:    train_time=80.18s±0.06 (N=3), val_loss=3.2774±0.0004, p(mean<3.28)=0.00341 -> VALID
  decompose:   the REAL convergence curve (mean over 3 runs), with instantaneous per-step:
               step    val_loss   cum_time   per-step(this segment)
                  0     10.830     0.00 s     —
                250      4.463     7.99 s     32.0 ms   (Stage 1: batch 131072, seq 896)
                500      4.148    16.97 s     35.9 ms
                750      3.735    31.33 s     57.5 ms   (Stage 2: batch 262144, seq 2048)
               1000      3.460    47.89 s     66.2 ms
               1250      3.331    68.83 s     83.8 ms   (Stage 3: batch 393216, seq 2048)
               1385     3.2774    80.18 s     84.0 ms   <- crosses 3.28 ONLY here, margin 0.0026
  read:        CONVERGENCE-BOUND AT THE TAIL. The loss is still 3.331 at step 1250 (0.051
               ABOVE target) and only crosses 3.28 in the final 135 steps — and those steps
               are the EXPENSIVE ones (~84ms vs ~32ms early), because batch+seq ramp across
               the 3 stages. The final 135 steps cost 11.4s (14% of wall) and earn 0.054 of
               loss at a razor 0.0026 margin. Naive step cuts are impossible.
  lever:       two real openings — (a) make the curve reach 3.28 EARLIER (convergence) so the
               expensive tail steps can be cut; (b) shave per-step. (a) is the big lever.
  decision:    this is the floor to beat; it already certifies at N=3, no work needed on it.
  → next:      profile ONE Stage-3 step to see the per-step split, then commit to a lever.
```

### OP 1 — profile one expensive step, pick the lever by the numbers

```bash
# nsys/ncu/nvprof are NOT installed on the H100 image — torch.profiler only.
# wrap steps 1250-1255 (Stage 3, the expensive regime) via the existing env hooks:
ssh h100 'cd /scratch/nano && NANOGPT_PROFILE_START=1250 NANOGPT_PROFILE_END=1255 \
  DATA_PATH=/scratch/nano OMP_NUM_THREADS=8 \
  /scratch/nano/envs/sr210/bin/python -m torch.distributed.run --standalone --nproc_per_node=8 train.py'
```

```text
PROFILE — one Stage-3 step (~82 ms), real per-step split (research/PROFILE.md):
  GEMM (aten::mm)            32.3 ms total / 24.2 self   <- MLP + projections dominate
  ReLU²-MLP Triton kernels   20.9 ms self
  FA3 backward                9.6 ms   ; FA3 forward 2.0 ms
  optimizer step              5.6 ms total / 4.6 self
  NCCL all-gather + r-scatter 2.2 + 1.7 = 3.9 ms         <- comms is NOT the wall
  "Command Buffer Full"      14.0 ms (604 launches / 5 steps)  <- launch overhead
  read:   per-step is GEMM/MLP + FA3 + ~14ms launch bound; comms is small. But this recipe
          is #83 — per-step is already near-roofline. Per-step shaving is the 1-2% game.
  decision: ATTACK CONVERGENCE FIRST (cut tail steps), per-step SECOND. This is the exact
            fork the live run got wrong (it went straight to FP8/MLP per-step precision).
```

### OP 2 — the BIG lever: convergence (named knobs, one change each, read the CURVE)

Each experiment is a single-variable edit to `solution/train.py`, scored `./eval_solution.sh
solution 3`, and judged on **whether the curve crosses 3.28 earlier at step 1250**, not just
on the final number. The real knobs (line-cited):

```text
a) NorMuon regularization — lr=0.023 (train.py:1858), weight_decay=1.2 (:1861),
   beta2=0.9 (:1860), momentum schedule warmup 300 / 0.85→0.95 (:1780).
   Try lr {0.021,0.025}, wd {1.0,1.5}: does val_loss@1250 drop below 3.331?
b) *** ADD annealed SGLD/Langevin noise to the NorMuon update *** — the seed has NO noise
   hook (grep confirmed none). Injected, annealed gradient noise is one of Recursive's five
   inventions and the single highest-value convergence bet here: it regularizes → reaches
   the target in fewer steps. This is a NEW mechanism in the optimizer, not a knob twist.
c) Schedule — 3-stage lr_mul 1.0/1.52/1.73 (:1765-1770), cooldown_frac=0.60 (:1777, anneal
   to 0.15·LR). Try pulling the cooldown earlier or lifting Stage-3 lr: earn the same tail
   quality in fewer expensive (84ms) steps.
d) The cut — IF any of the above makes val_loss<=3.28 by ~step 1300, then drop
   num_scheduled_iterations 1375->~1290 (:1680) and re-measure. ~85 tail steps × 84ms ≈ 7s.

  CAND normuon_sgld_noise — (b), on the seed floor
    command:     edit train.py NorMuon update; ./eval_solution.sh solution 3
    measured:    <train_time, val_loss, p from the scorer — to be run>
    read:        WIN iff the curve crosses 3.28 EARLIER (val_loss@1250 < 3.331) at equal/less time
    lever:       convergence (fewer steps) — the big lever
    decision:    keep if it moves the crossing earlier; then STACK (c)+(d) onto it, co-tuned
    → next:      stack winners; the moment mean<<3.28 at N=3, certify
```

> The live run never edited any of (a)–(d). It produced its speedups entirely on the
> per-step/precision side, which is why it landed at 79.77s and not below — the big lever
> went unworked. The first thing my run does differently is spend its first missions here.

### OP 3 — the statistical discipline (how the win actually banks)

```text
CAND <stacked recipe> — mean clearly inside the gate at N=3
  command:     ./eval_solution.sh solution 3   ->   ./eval_solution.sh solution 10
  rule:        the moment N=3 shows mean val_loss CLEARLY < 3.28 with small sd, CERTIFY at
               N=10 and BANK as the new floor. Do NOT keep engineering validity you have.
  evidence:    this is exactly what won tonight: hybrid post-only MLP read INVALID at N=3
               (val_loss 3.2765, p=0.01313) — a POWER miss, not a quality miss — and the
               SAME recipe certified VALID at N=10 (val_loss 3.2776±0.0022, t=−3.389,
               p=0.004007, 79.77s±0.06) with ZERO recipe change.
  stack:       keep a current-best recipe; ADD each winning mechanism onto it and co-tune;
               revert only a knob that measurably hurts. Never revert to the bare floor.
```

### OP 4 — only now the small lever: per-step (co-tuned onto the best stack)

```text
- leaner / fused ReLU²-MLP activation storage — the live run's one real win
  (mlp_post_only 79.55s val_loss 3.2771; this is ≈ one of Recursive's inventions). KEEP it.
- cut the ~14ms "Command Buffer Full" launch overhead: fuse the many small kernels / use
  CUDA graphs for the resident step.
- FP8 — only with DELAYED/CACHED scaling. The live run proved naive FP8 here is net-negative:
  fixed-scale diverged (val_loss 7.69), dynamic per-step amax was SLOWER (81.29s) because the
  amax reductions ate the GEMM win. Low priority, structural; fix scaling-fusion or skip.
```

## The nails — what the live run actually did (do NOT repeat)

The procedure above is the known-good path. The agent's real run hit these; the expensive
failures on a speedrun are **process and statistics**, not the recipe.

- **Nail 1 — the perpetual-prototyper loop.** ~6 missions over ~3 hours, each `{take floor →
  build one variation → N=3 → miss by a hair → revert to floor → done}`, floor stuck at
  ~80.16s while a quality-valid 79.7s near-win sat un-certified. A "successful" mission that
  neither moves the floor nor certifies a near-win is churn dressed as progress.
- **Nail 2 — reading a POWER miss as a QUALITY miss.** `p=0.085` with `val_loss=3.2771`
  (clearly < 3.28) was treated as "engineer the recipe harder." It was underpowered, not
  low-quality. The fix for a power miss is more seeds (N=10), not another mechanism.
- **Nail 3 — wrong lever for hours.** All real work went to per-step FP8/MLP precision (the
  small, roofline-bounded lever) while the optimizer/schedule/noise (the big, compounding
  lever, §OP 2) went untouched — so the run stayed ~2.5s short of 77.3s.
- **Nail 4 — testing in isolation and reverting to the bare floor.** Each mechanism was
  measured against the seed and reverted, so nothing compounded. Recursive's 77.3s is FIVE
  stacked inventions; the right unit of work is a current-best STACK.
- **Nail 5 — infra/operational.** A daemon self-handoff once lost the persisted Manager
  classification; a restart landed mid-N=10-recert and marked that mission `failed`
  even though the orphaned scorer finished valid — the win computed but did not bank cleanly.
  Restart only at clean mission boundaries; verify env + `import` first.

## How to apply this chain to another speedrun
1. OP 0: score the seed (`./eval_solution.sh solution 3`) with `set -o pipefail`, and extract
   the real convergence curve. Never fabricate `train_time`/`val_loss`/`p`.
2. OP 1: profile one expensive step; read whether per-step is compute/MLP/comms/launch bound,
   and whether per-step is already near-roofline (a near-SOTA seed usually is).
3. Pick the lever by leverage: **convergence (fewer steps) first**, per-step second. Name the
   exact knobs from the recipe.
4. For each candidate, record the full chain block with real scorer numbers; on every INVALID
   classify QUALITY vs POWER.
5. The moment a mean is clearly inside the gate, **N=10-certify and bank**; **stack, don't
   revert**.
6. Stop a line when the gap to the next anchor needs an invention you don't have, and say so.

## Response shape
- State the exact scorer command and whether it exited 0; the seed's measured baseline and curve.
- Show the decomposition (per-step by stage, where the curve crosses) and which lever you chose, why.
- For each candidate, the full chain block with real `train_time/val_loss/p`; each INVALID
  labeled QUALITY or POWER.
- Quote the frozen `SCORE` line for every banked (N=10) candidate.
- Frame the result honestly against the same-harness baseline and named anchors, like-for-like.
