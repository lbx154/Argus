---
name: "NanoChat Autoresearch Hands-on Trace"
description: "{'A concrete NanoChat-autoresearch (Recursive \"First Steps\" Task 1) process trace on a single B200, written as the operator\\'s OWN known-good operating procedure for the DUAL of a speedrun — FIX the 300s single-GPU budget, MINIMIZE the mean validation bits-per-byte (val_bpb) over N seeds, editing only train.py against a frozen scorer. The spine is a per-candidate measured causal chain bound to real numbers from a real 50-candidate live run (vanilla@64 1.053 → floor 0.9890)': \"measured(val_bpb mean±sd over N seeds vs the SEED-NOISE floor) → decompose(effective_tokens-in-300s × quality-per-token; where on the loss curve does 300s land) → pick the binding lever (throughput vs per-token quality) → ONE coordinated change → re-measure → classify miss (real signal vs sub-noise jitter) → bank / stack-as-a-BUNDLE. The live run's two expensive errors — deciding accept/reject on differences smaller than its own seed noise, and testing single levers greedily so synergistic structure never assembled — are demoted to the nails. Does NOT contain the reference SOTA recipe.\"}"
---

## Title
NanoChat Autoresearch Hands-on Trace

## Description
A **human-written seed exemplar** of how to optimize a *fixed-budget* LM-pretraining
benchmark — **minimize the validation loss (val_bpb) reachable in a frozen 300-second
single-GPU budget**, the **dual** of a wall-clock speedrun. It is written deliberately as the
**known-good operating procedure** — the concrete commands and the lever *classes* I would
actually work, with the decision at each fork — *not* a transcript of the agent's live run.
That run is real and is mined here for its **measured numbers** (a 50-candidate trajectory
from vanilla to a 0.9890 floor) and for its two expensive mistakes, which are recorded as the
**nails**: it spent ~25 candidates deciding keep/reject on differences **smaller than its own
seed-to-seed noise** (coin flips dressed as progress), and it tested every mechanism as a
**single greedy lever against the floor**, so the **coordinated structure** the frontier
needs never assembled. The spine is a per-candidate **measured causal chain** where every step
is bound to a real `val_bpb` from the frozen scorer and the next move follows from **the
binding constraint**. This file does **not** reproduce the reference optimized recipe — it
teaches the method that *derives* one.

## When to use
- The task is a **fixed-compute-budget** from-scratch LM run (e.g. nanochat Task 1: a small GPT
  trained for **exactly 300s on one B200**, scored by **mean `val_bpb` over N seeds** on a
  frozen held-out shard), editing only `train.py` against a frozen `lib.py`/scorer.
- The agent must produce real per-seed `val_bpb` numbers and run the **measure → decompose →
  pick-lever → re-measure → bank/stack** loop with a **seed-noise-aware** keep/reject gate.
- A from-scratch-LM optimization is stuck: many single-knob screens, floor barely moving,
  improvements the size of run-to-run noise being banked as wins.

## When NOT to use
- You only need the high-level methodology and toolkit. Use `NanoChat Autoresearch SOTA
  Optimization`.
- The objective is wall-clock-to-a-target (use `Speedrun Hands-on Trace`), a single GPU kernel
  (`SOL Kernel Hands-on Trace`), a paper matrix, or RL/post-training.
- The scorer / GPU is unavailable — write a setup/blocker report; never fabricate a `val_bpb`.

## The one rule this trace exists to teach

> **Under a fixed budget the score is `quality(per-token) × effective-tokens(in 300s)` — attack
> whichever binds — and a lower number is not a win until it clears your SEED NOISE and is part
> of a STACK.** Two failure modes kill this task and the live run hit both. (1) Once the floor
> is improving by less than the run-to-run sd of a single seed, your keep/reject is a **coin
> flip**; banking those "wins" builds a floor out of lucky seeds. (2) The frontier here is a
> **coordinated bundle** of levers (a reshaped capacity allocation co-tuned with the update and
> the output head); many of its pieces **regress in isolation**, so a greedy "one category
> change vs the floor" search rejects each piece and **never reaches the combination**. Measure
> your noise first; then search **bundles**, not single knobs.
>
> **And a rule precedes even these: you are knowledge-limited, so RETRIEVE before you build.**
> Reciting a lever from memory gets its coefficients/conditions wrong; go read the concrete prior
> art for each lever class (the optimizer, capacity-allocation / scaling-law, init/residual-scaling
> work) and reproduce it. The frontier here is a *recombination of KNOWN, retrieved levers*, not a
> never-seen trick — see `NanoChat Autoresearch SOTA Optimization` §0. (Anti-cheat: general
> technique only, never the reference recipe.)

## Run economy — one confident screen, not a seed farm

One official experiment spends the complete 300-second training budget: **at least five minutes
of GPU time, plus compile and evaluation overhead**. The default candidate protocol is therefore
**one clean run / one seed**. Diagnose the binding constraint, choose a mechanism you believe in,
run it once, and make a decisive research call from the measured trajectory and endpoint.

Do not spend 3, 5, or 10 repeated seeds on every ordinary candidate. Use a small repeat set once
to calibrate the retained baseline; repeat a candidate only when its first clean result is
mechanistically credible and clearly promising, near the live best, or ready for final
certification. A clear regression needs no repeat. A sub-noise near-tie is not promoted; either
move to the next stronger idea or reserve confirmation for the rare candidate worth the extra
GPU budget. **Confidence means committing to a diagnosis-backed experiment, not buying certainty
with repetitive runs or declaring noise a win.**

## What "process data" means here — the chain to learn

Record each candidate as one linked block. Learn the shape; it is a teaching exemplar, not a
rigid form a harness greps for:

```text
CAND <name, ON TOP OF which floor, and which lever CLASS(es) it changes>
  command:     ./eval_solution.sh train.py <N>     (real flash_attn.cute FA-4, DEVICE_BATCH_SIZE=64,
                                                     SEQUENTIAL seeds, shard_06542 val)
  measured:    val_bpb = <per-seed values>; mean=<m> ± sd=<sd> over N=<n>     [LOWER better]
  noise-gate:  is (floor − mean) > ~2-3× seed-sd?   REAL signal | SUB-NOISE jitter
  decompose:   300s lands at step≈<s> / token≈<T>; per-step≈<ms>; loss-curve value at 300s.
               which side moved — effective tokens-in-300s (throughput) OR quality-per-token?
  read:        better/worse than floor, and by how many σ? if regression, is the lever
               wrong, or right-but-only-synergistic (regresses ALONE, helps in a bundle)?
  lever:       which CLASS this change attacks: {optimizer | capacity-allocation (depth/width/
               bottleneck) | normalization/residual numerics | output-head shaping |
               effective-update (init/reg/schedule) | data order | throughput}
  decision:    bank as new floor | KEEP IN THE STACK & co-tune the next lever onto it |
               revert this lever | hold as a synergy-candidate to retry inside a bundle
  → next:      next COORDINATED change, chosen by the binding constraint
```

## 2026-07-17 collaborative autoresearch-at-home B200 trace

This second real trace sharpened the procedure for the community
`autoresearch-at-home` scaffold. It is evidence about operating the loop, not a recipe to copy.

### Detect the scaffold before enforcing invariants

Two legitimate nanochat shapes exist:

- **Collaborative at-home:** editable `train.py`; frozen `prepare.py`; local
  `coordinator.py`; `results.tsv`; data/tokenizer under `~/.cache/autoresearch`.
- **Legacy benchmark:** editable solution/train artifact; frozen `lib.py`; external runner.

Freeze the harness that actually exists. Requiring `lib.py` in a `prepare.py` repository creates
fake setup work; treating `prepare.py` as editable invalidates the score. Never hard-code another
project's absolute path: use the canonical workdir supplied by the runtime, and do not `cd` into a
different campaign's repository.

### A swarm best is source material until the local verifier reproduces it

The collaborative loop is:

```text
THINK from live results/insights/hypotheses
→ pull hardware-tier/global best source
→ reproduce locally under the frozen scorer
→ CLAIM before editing
→ run
→ PUBLISH result + insight + next hypothesis, including discard/crash
→ refresh the live best every five experiments
```

Adopting source is not adopting a number. In this trace the live best was `0.899885` from a B200
shared-trigram source, but the source imported FA-4 components absent from the repository lock.
The first local launch therefore crashed before training. FA3 also loaded but had no SM100 kernel
image. Neither event was a model result, and substituting SDPA/FA3 would have changed the
benchmark.

After explicit operator authorization, the runtime was made reproducible by pinning the missing
Python distributions and a concrete upstream FA-4 source revision, then smoke-testing real B200
forward+backward before the full scorer. One observed compatible set was
`apache-tvm-ffi==0.1.12`, `einops==0.8.2`, `nvidia-cutlass-dsl==4.4.2`,
`kernels==0.16.0`, plus the legacy FA-4 Hub source revision
`7f952e7e7ec1787ad1f7d209d0bdefdb34747af2`. This is historical provenance, not a forever-latest
recommendation: pin the exact set that the adopted source demonstrably runs with.

### Process-repeat variance is not seed variance

Three fresh sequential processes of the retained artifact, each with default seed 42 and the
unchanged 300-second scorer, produced:

```text
0.903871, 0.903664, 0.903917
mean = 0.9038173333333333
same-seed process-repeat sample sd = 0.000134767701
```

This establishes execution repeatability on that host. It does **not** estimate cross-seed
variance. Record both separately when seeds are part of the protocol; never replace either with
a generic `0.001` or `0.002` folklore constant. A near-tie cannot be banked until it clears the
locally measured gate appropriate to the claim.

Security scrubbing can rewrite completed text logs. If that changes file digests after the run,
update the manifest's `source_log_sha256` values from the scrubbed artifacts and rerun the
consistency check. Do not waste another 300-second scorer run when the numeric evidence is already
valid and only the provenance metadata is stale.

### Throughput bought more steps but deleted quality-per-token

The profiler identified matrix work as the throughput lever. A preregistered architecture screen
halved MLP expansion only in layers 0–3 while preserving late-layer width:

```text
retained:   94.4M params, 3141 steps, 463.2M tokens, val_bpb 0.904052
candidate:  84.9M params, 3387 steps, 499.4M tokens, val_bpb 0.905753
```

The candidate bought about 7–8% more updates and materially less VRAM, but regressed BPB. The
throughput mechanism was confirmed; the research hypothesis was refuted. Early MLP capacity was
not expendable: extra tokens did not repay the quality-per-token loss. The updated direction is
capacity-preserving compute reduction — sharing, factorization, or another co-designed mechanism
that reduces backward cost without deleting representational width.

This is the dual decomposition doing its job: a faster step is only useful if the net endpoint
improves. Throughput is evidence, never the reward.

## My run: the concrete operations

Project: `/home/argustest/nanochat-mission-b200`. Scorer (frozen):
`./eval_solution.sh train.py <N>` — ships `train.py` to a single B200 (`ssh -p 2231`,
interp `/opt/conda/envs/ptca/bin/python`, **real `flash_attn.cute` FA-4 sm_100**), runs the
candidate for a **fixed 300s** under `DEVICE_BATCH_SIZE=64` + grad-accum, **N seeds
SEQUENTIALLY** (concurrency on the shared pod collapses throughput and inflates `val_bpb`
~0.08–0.10 — a measured confound, NOT noise), evaluates `val_bpb` on the held-out
`shard_06542`, prints each seed's `val_bpb` + `MEAN_VAL_BPB`. **LOWER is better.** Anchors
(single B200, re-measured/published): vanilla 1.0587 (my `=64` re-measure 1.053); reference
"optimized_from_vanilla" 0.9344; Recursive best 0.9109.

### OP 0 — RETRIEVE the prior art, then establish the baseline AND the noise floor

Before any edit I retrieve the prior art for the lever classes I'll work — I am knowledge-limited
and reciting levers from memory gets them wrong (general technique ONLY; the reference recipe is
off-limits and searching for it is disqualifying):

```text
WebSearch "Muon Newton-Schulz orthogonalized momentum Moonlight RMS-match"     # optimizer (§7 big lever)
WebSearch "Chinchilla compute-optimal scaling laws" ; "transformer depth vs width allocation"
WebSearch "muP muTransfer learning-rate transfer small proxy"                   # cheap LR tuning
WebSearch "DeepNet Fixup ReZero residual init scaling deep transformer"         # a biggest late jump
WebSearch "logit softcap Gemma-2 z-loss" ; "sliding window local attention"     # head shaping; throughput
WebSearch "Fantastic Pretraining Optimizers equal tuning"                       # any multiplier is an UPPER bound
```

I keep the most concrete form (coefficients, conditions), corroborate any quoted gain, and treat
it as a hypothesis to reproduce — not a fact. THEN measure the two ground truths below.

The speedrun has a t-test as its validity gate; this task hands you **raw per-seed numbers**
and makes the noise gate **your** job. Skipping it is the single most expensive omission (nail 1).

```bash
cd /home/argustest/nanochat-mission-b200
./eval_solution.sh train.py 1            # vanilla baseline, 1 seed (fast signal)
# ONE-TIME calibration — enough to estimate whether the instrument is stable:
./eval_solution.sh train.py 3            # SAME vanilla, 3 seeds -> initial seed-to-seed sd
# Extend to 5 only if N=3 is unstable or a final claim needs tighter uncertainty.
```

```text
CAND vanilla@64 — baseline + NOISE FLOOR (no edits)
  measured:    mean val_bpb ≈ 1.053 (ref 1.0587, like-for-like at DEVICE_BATCH_SIZE=64)
  noise-gate:  ESTABLISH IT NOW. A single-seed val_bpb on this task is NOT reproducible to 4
               decimals. Until you know seed-sd, you cannot tell a 0.001 "win" from a coin flip.
               (The live run never ran this and paid for it — see nail 1. Its own later decisions
               hinged on 0.0005-level deltas, which is below any plausible seed-sd.)
  decompose:   300s buys a fixed token budget = (300s − warmup/compile) / per_step. Vanilla
               spends it at depth-12 / DEVICE_BATCH_SIZE=64 / 2^17 total-batch / LR 9e-4, real
               FA-4. The loss curve at 300s is FAR from converged — this is a small-data-pass
               regime, so BOTH levers (more effective tokens, better per-token quality) are open.
  read:        this is the floor to beat. The reference 0.9344 is ~0.12 below it — a LARGE gap,
               not a knob-tweak gap.
  lever:       n/a — set the floor + the noise σ.
  decision:    floor = 1.053; promotion gate = "(floor − mean) > ~2-3σ_seed", measured here.
  → next:      reframe the budget (OP 1), then spend the FIRST candidates on the biggest lever.
```

### OP 1 — the dual reframe: "what minimizes bpb in 300s", not "what makes the model better"

```text
REFRAME (the mental flip that orders everything):
  budget is FIXED at 300s -> effective_steps = 300s / per_step_cost
  score = the quality (bpb) the recipe reaches in effective_steps
  => two levers, and like a speedrun they are NOT equal:
     (A) MORE EFFECTIVE TOKENS IN 300s  = throughput × per-token sample-efficiency.
         A cheaper step (faster attention/MLP, the windowed-attention class, leaner numerics)
         is NOT a "quality knob" here — it BUYS MORE STEPS, which is quality under a fixed clock.
     (B) BETTER QUALITY PER TOKEN = the optimizer, the capacity ALLOCATION (depth vs width vs
         a bottleneck), normalization/residual numerics, the output head, init/reg/schedule.
  the live run treated (A)-class levers (e.g. windowed attention) as standalone quality knobs
  and missed that on a 300s clock they compound with (B). Frame every candidate as "does this
  net MORE quality per 300s," and the lever ordering falls out.
```

### OP 2 — the BIG levers, by CLASS, biggest-first — and the real measured wins

Each experiment is a change to `train.py`, scored `./eval_solution.sh train.py 1` for signal,
and judged on **whether mean clears the floor by > ~2-3σ_seed**, not on the 4th decimal. Work
the lever **classes** in rough leverage order (the live run actually swept these — its *real*
above-noise wins are cited as the trace's measured data; the *order* is what I would fix):

```text
1. OPTIMIZER (biggest single lever for fixed-budget from-scratch LM).
   Newton-Schulz-orthogonalized momentum (Muon) on the matrices was the first real jump:
     CAND muon  ->  1.053 → 1.028   (REAL, ~-0.025, far above noise)
   This is the highest-value single change on this task. Adam-mini / Lion / Sophia variants and
   the Muon/Adam split across parameter banks live here too — but most REPLACEMENTS of a working
   Muon regress (all-Lion, Sophia-core both measured ~+0.04 worse): Muon is the strong base.
2. CAPACITY ALLOCATION at ~fixed params (depth vs width vs a narrow bottleneck).
   Reshaping where the parameters sit was the most productive architecture axis:
     CAND wide_shallow_arch       ->  1.0164 → 1.0121   (REAL)
     CAND scaled_width_shallow    ->  1.0121 → 1.0052   (REAL)
   but its NEIGHBORS regress alone (deep-narrow +0.03, ultra-shallow ≈ floor) — a strong signal
   that the optimum is a CO-TUNED point, not a single dial (this is the synergy clue, OP 4).
3. EFFECTIVE-UPDATE NUMERICS — init/residual scaling, schedule, the loss/logit shaping.
   Two of the largest late jumps were here, and they were UNTAPPED for ~30 candidates:
     CAND scaled_residual_init    ->  1.0019 → 0.9927   (REAL, ~-0.009, FIRST sub-1.0)
     CAND logit_softcap           ->  0.9927 → 0.9890   (REAL)
   Lesson: high-leverage levers can sit unexplored while the search grinds saturated ones.
4. NORMALIZATION / RESIDUAL numerics — norm placement, QK/value normalization.
     CAND sandwich_norm           ->  1.0041 → 1.0026   (real but small)
     CAND value_norm_attention    ->  1.0026 → 1.0019   (real but small — near the noise edge)
5. DATA ORDER — shard ordering / curriculum (real but small, saturates ~one bit).
6. THROUGHPUT (lever A) — windowed/local attention, leaner numerics: buys steps under 300s.
   Local-global attention was an early real win (1.0203 → 1.0192); treat throughput wins as
   step-budget, then re-spend the steps on lever (B).

  CAND <next coordinated change> — on the current STACK
    command:     edit train.py (ONE lever class, or a small co-designed bundle — OP 4);
                 ./eval_solution.sh train.py 1
    measured:    <mean ± per-seed; compare to floor by σ, not by decimals>
    read:        REAL (>2-3σ) keep; SUB-NOISE -> do NOT bank, it's a coin flip
    lever:       <class>
    decision:    bank & KEEP IN STACK; co-tune the next class onto it
    → next:      the next class, or close a synergy bundle (OP 4)
```

### OP 3 — the noise discipline (how a win actually banks here)

```text
CAND <candidate near the floor>
  rule:        a keep/reject is only valid if |floor − mean| > ~2-3 × σ_seed. If the delta is
               smaller, it is a COIN FLIP — re-screen at higher N (the seeds are sequential, so
               N=3 costs ~3×300s+overhead) before banking, or do NOT bank it at all.
  worked nail: the live run banked a039 0.001860 over a038 0.002580 (Δ=0.0007) and rejected
               a040 0.002330 / a041 0.002640 — ALL on deltas of 0.0002-0.0008. With a seed-sd
               plausibly ≥0.001, those promote/reject calls were noise. ~5 hours / ~25 candidates
               (a022→a046) shuffled inside a ~1.002-1.005 band that was statistically ONE point.
  operator role: the daemon screens at 1-seed; the OPERATOR runs a SMALL multi-seed confirmation,
               and ONLY for a SOTA-level candidate. Do not multi-seed every screen; do not bank a
               sub-noise screen as a floor. Start at N=3; extend only when the evidence warrants it.
  stack:       maintain a current-best train.py; ADD each REAL winning lever onto it and co-tune;
               revert only a lever that measurably (>σ) hurts. Never revert to bare vanilla.
```

### OP 4 — search BUNDLES, not greedy single levers (the move that crosses the last gap)

```text
The frontier is a COORDINATED stack: a reshaped capacity allocation (depth/width + a bottleneck)
co-designed WITH the output head and the update. Its pieces have a property that breaks greedy
search: SEVERAL REGRESS IN ISOLATION and only pay off TOGETHER.

  evidence from the live run (real measured regressions of pieces tried ALONE):
    deep-narrow arch    +0.03   (a narrow trunk ALONE underfits)
    MoE / factorized    +0.03   (capacity reshape ALONE, uncoordinated, regresses)
  greedy "one category change vs the floor" REJECTS each of these -> never reaches the combo.

  the fix (what I would do that the live run did not):
    - propose a 2-4 lever BUNDLE as ONE candidate, co-designed by a hypothesis ("reshape the
      trunk narrower AND widen the output path AND scale the residual init for the new depth"),
      screen the BUNDLE, and only THEN ablate WITHIN the winning bundle to find who carries it.
    - keep a synergy-shortlist: any lever that regressed ALONE but is plausibly synergistic gets
      retried INSIDE a bundle, not discarded.
    - this is the search change that turns "single-lever wins are exhausted at ~0.99" into "the
      coordinated structure that the reference 0.93 frontier is."
```

## The nails — what the live run actually did (do NOT repeat)

The procedure above is the known-good path. The agent's real 50-candidate / ~18-hour run hit
these; the expensive failures on a fixed-budget LM run are **measurement and search shape**,
not the individual tricks (most of which it found correctly).

- **Nail 1 — deciding below the noise floor.** It never measured seed-sd, then banked/rejected
  candidates on 0.0002–0.0008 `val_bpb` deltas (a039 over a038; a040/a041 rejected) — almost
  certainly inside run-to-run noise. ~25 candidates (a022→a046, ~5h) shuffled within a band that
  was statistically a single point. **Measure σ at OP 0; gate at 2-3σ.**
- **Nail 2 — greedy single-lever search.** Every mechanism was a lone "category change vs the
  floor." Levers that only pay off TOGETHER (narrow trunk + wide head + matched init/residual
  scaling) each regressed alone and were correctly-but-fatally rejected, so the coordinated
  ~0.93 structure was unreachable. **Search bundles (OP 4).**
- **Nail 3 — leaving the high-leverage lever for last by accident.** Residual/init scaling and
  logit shaping (two of the biggest jumps, 1.0019→0.9927→0.9890) sat untouched for ~30 candidates
  while the search ground saturated norm/optimizer/data knobs. Order the **classes** by leverage
  up front, don't reach the big one on candidate #47 by luck.
- **Nail 4 — the eval-harness flaky-launch thrash (infra, ~30% of several missions).** A flaky
  port-forward dropped the launcher's "LAUNCHED" echo → the scorer false-failed → the retry
  `rm`-wiped the in-flight seed's log and relaunched, burning 20–35 min on candidates whose model
  was fine. Harden the launcher to confirm by the node's REAL state (a tagged, idempotent
  attach), not by a lossy echo, BEFORE optimizing the model.
- **Nail 5 — a frozen reviewer froze the whole pipeline.** A schema went strict-incompatible
  (a property added to the output schema but not to its `required` array) and every reviewer call
  errored; missions could not close and the best win sat un-banked for ~40 min until the schema
  was made compliant. On a fixed-budget run the model can be right while the *harness* is the
  bug — read the actual backend error, don't treat a stalled mission as a model failure.
- **Nail 6 — not reframing to the dual.** Throughput-class levers (windowed attention) were
  judged as standalone quality knobs, missing that under a 300s clock they BUY STEPS. Frame
  every candidate as "more quality per 300s" (OP 1).

## How to apply this chain to another fixed-budget LM run
1. OP 0: score the baseline, AND measure the seed-sd (`./eval_solution.sh train.py 5`). Never
   fabricate a `val_bpb`; never bank below noise.
2. OP 1: reframe to the dual — effective-tokens-in-budget × quality-per-token; decide which binds.
3. OP 2: work the lever **classes** biggest-first (optimizer → capacity allocation → effective-
   update numerics → norm → data → throughput); name the class each candidate changes.
4. OP 3: gate every keep/reject at 2-3σ_seed; multi-seed-confirm only SOTA-level candidates; stack.
5. OP 4: once single-lever wins thin out, propose **co-designed bundles**, screen the bundle, then
   ablate within it; keep a synergy-shortlist of regressed-alone levers.
6. Fix the eval harness FIRST (idempotent, node-state-confirmed launch) so each screen is clean.
7. Stop when the gap to the next anchor needs coordinated structure you have not yet co-tuned —
   and say so, with the σ-honest distance to it.

## Response shape
- State the scorer command and whether it exited 0; the baseline mean AND the measured seed-σ.
- Show the dual decomposition (where 300s lands on the curve; throughput vs quality-per-token)
  and which lever class you chose, why.
- For each candidate, the full chain block with real per-seed `val_bpb`; label every result
  REAL (>2-3σ) or SUB-NOISE; never bank sub-noise.
- For regressions, state whether the lever is wrong or right-but-synergistic (retry-in-bundle).
- Frame the result honestly against the same-harness baseline and the named anchors (0.9344 /
  0.9109), like-for-like, with the σ-honest distance — and never reproduce a reference recipe.
