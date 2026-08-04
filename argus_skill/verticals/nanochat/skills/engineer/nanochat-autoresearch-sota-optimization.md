---
name: "NanoChat Autoresearch SOTA Optimization"
description: "{'A senior-researcher methodology for FIXED-BUDGET from-scratch LM-pretraining benchmarks (nanochat Task 1 and kin) — the DUAL of a speedrun': \"the 300s budget is frozen, you MINIMIZE the mean val_bpb reachable in it. The budget as effective-tokens × quality-per-token, the bottleneck taxonomy (sample-efficiency-bound vs throughput-bound vs optimization-bound vs capacity-misallocation-bound vs numerics-stability-bound), where-does-the-budget-go diagnosis, the SEED-NOISE validity discipline (measure σ / gate keep-reject at 2-3σ / never bank below noise / multi-seed-confirm only SOTA candidates / stack-don't-revert), a leverage-ordered toolkit (optimizer & capacity allocation first, throughput/numerics as step-budget), the optimizer/architecture prior library, and the BUNDLE (non-greedy) search the frontier requires. Distilled human expertise to learn, NOT a recipe to copy — it does not contain the reference SOTA solution.\"}"
---

## Title
NanoChat Autoresearch SOTA Optimization

## What this is
This is **distilled human expertise** — how a senior ML-systems researcher thinks about a
**fixed-compute-budget** from-scratch LM run, not a checklist. Read it for the mental model and
the priors; do not execute it mechanically and **do not expect it to hand you the answer** — it
deliberately teaches the *method* that derives a frontier recipe, not the recipe. The numbers
are real (a 50-candidate B200 run from vanilla `val_bpb≈1.053` to a `0.989` floor) and exist to
teach; the method generalizes across budgets and metrics. When a real run teaches you something
sharper, evolve this. (Companion: `NanoChat Autoresearch Hands-on Trace` is one fully-worked,
failure-first example of the loop.)

## When to use
- The objective is to **minimize a quality metric** (val loss / `val_bpb`) reachable in a
  **fixed compute budget** (e.g. **300s on one B200**, scored by **mean over N seeds** on a
  frozen val shard), editing only `train.py` against a frozen scorer (`lib.py`). This is the
  **dual** of a wall-clock speedrun.
- The task names an editable training recipe, a frozen scorer/budget/val-shard, and a numeric
  quality metric — nanochat-style autoresearch and kin.

## When NOT to use
- The objective is wall-clock-to-a-target (use `Speedrun SOTA Optimization`), a single GPU
  kernel (`SOL Kernel SOTA Optimization`), a paper matrix, or RL/post-training.
- The scorer/GPU is missing — write a setup/blocker report; do not invent a `val_bpb`.

---

## 0. Research-first: you are knowledge-limited — RETRIEVE before you build

A senior researcher's first move is not to think harder; it is to **go read what humans already
figured out**. You have a sharper reason: your knowledge is *parametric* — frozen at cutoff,
capacity-bounded, weakest on exactly the long-tail facts that decide this task (which optimizer
wins from-scratch, how to allocate capacity, init/residual scaling for a given depth) — and you
are trained to sound confident, so from memory you emit plausible-but-wrong technique facts. The
evidence is blunt: on agentic-research benchmarks frontier agents largely **cannot re-implement
even known gains** (Automated LLM Speedrunning Benchmark; FIRE-Bench < ~50 F1, failing at
experimental design + evidence-grounding, not coding). The binding skill is **retrieve →
reproduce → verify**, not raw cleverness.

**Invention is recombination, so retrieve and stack.** Bibliometrics (Nature 2022) show
unprecedented ideas are rare; almost all progress is *novel re-mixing of validated parts*. The
frontier recipe here is a **co-designed bundle of KNOWN levers** (an orthogonalized optimizer +
a reallocated capacity + matched init/schedule/head) — not a never-seen trick. Your job is to
retrieve the menu (§7) in its concrete form and assemble it; reciting a lever from memory gets
its coefficients/conditions wrong.

**The discipline (search-then-build):** (1) reproduce the baseline AND the σ_seed floor on your
own box first (§3); (2) for each lever, chase the most concrete artifact — repo / ablation table /
reference implementation > prose > abstract; (3) corroborate any "X% better" against a second
source and treat it as an **upper bound under unequal tuning** (*Fantastic Pretraining
Optimizers*, 2509.02046: matrix-optimizer multipliers shrink at scale — tune your baseline as
hard); (4) one lever or one bundle, gated against σ_seed (§3), then stack.

**Anti-cheat line:** retrieve GENERAL technique, methodology, design *rationale*. NEVER retrieve
this task's reference/answer recipe — this guide deliberately omits it. Understanding *why* a
component helps is research; copying the answer is disqualifying.

**Search playbook (general technique only — feeds §7's lever classes):**
- optimizer: *Muon* / Newton-Schulz orthogonalization + *Moonlight* (RMS-match, decoupled WD); the Muon/Adam bank split
- capacity allocation: *Chinchilla* compute-optimal scaling laws; depth-vs-width tradeoffs; *μP/muTransfer* (tune on a small proxy, transfer); bottleneck/low-rank trunk designs
- init / residual numerics: depth-matched residual/init scaling (*DeepNet*, *Fixup*, *ReZero*); the warmup-stable-decay (*WSD*) schedule
- output head / logits: logit softcap (*Gemma-2*), tied/untied embeddings, *z-loss*
- throughput (buys steps under the fixed clock): sliding-window / local attention; FA-4 / fused kernels; lower-precision matmul
- the method: how to measure σ_seed and gate at 2-3σ; reading the loss-vs-step curve to tell sample-efficiency-bound from throughput-bound; a literature-grounded novelty check before calling a bundle "new"

---

## 1. The mental model: under a fixed budget, quality = effective-tokens × quality-per-token

A speedrun fixes the quality target and minimizes time. **This task is the dual: it fixes the
time (300s) and minimizes the loss.** To first order the val_bpb you can reach is set by:

```text
final_bpb ≈ f( effective_tokens_seen , quality_per_token )
effective_tokens_seen ≈ (budget − warmup/compile) / per_step_cost  ×  tokens_per_step
```

There are **two levers**, and like a speedrun they are not equal:

- **More effective tokens in the budget** — a *throughput* improvement. A cheaper step (faster
  attention, windowed/local attention, a leaner MLP, lower-precision matmul) is **not a quality
  knob here — it BUYS MORE STEPS**, and more steps under a fixed clock is lower loss. Bounded by
  the hardware roofline.
- **Lower loss per token** — a *sample-efficiency / quality* improvement: the optimizer, how the
  parameters are **allocated** (depth vs width vs a bottleneck), normalization/residual numerics,
  the output head, init/regularization/schedule. **This is the 10× lever and it compounds** —
  every per-token gain multiplies against the tokens the budget buys.

A fixed-budget run, like a kernel or a speedrun, **is bottlenecked by one of these and wastes
effort spent on the other.** The craft: find which binds, attack it, re-measure.

> The single most common error here is the mirror of the speedrun's: treating throughput levers
> (windowed attention, leaner numerics) as standalone "model quality" choices, OR grinding tiny
> per-token tweaks while the **capacity allocation** and **optimizer** — where the leaderboard is
> won — go un-reshaped. Frame every candidate as **"does this net lower bpb per 300s,"** and the
> lever ordering falls out.

---

## 2. Honesty rules (non-negotiable, the only hard floor)

- The **frozen scorer** is the only source of truth for `val_bpb`. A self-reported / training
  loss number is a hypothesis, not a result.
- **Real `flash_attn.cute` (FA-4) only.** No SDPA fallback, faked `kernels`, FA2, or any attention
  substitute. A score from a drifted environment (wrong attention, wrong lib.py/data/versions) is
  **INVALID, full stop** — it is not comparable to the reference numbers and must never be banked
  or reported as a result.
- **No fabricated `val_bpb`.** Every number is the scorer's, over the actual N seeds.
- **Do not game the metric.** Never special-case the val shard, hardcode/leak outputs, shortcut
  training, or alter the val data / loss / 300s budget / `DEVICE_BATCH_SIZE` / scorer. The budget
  and metric are frozen precisely so you cannot.
- **Seeds run SEQUENTIALLY.** Concurrent seeds on a shared pod collapse throughput and inflate
  `val_bpb` ~0.08–0.10 — a measured CONFOUND, not noise. An "isolated 1.01" and a "contended 1.11"
  are the same recipe; never compare across contention levels.
- Reproduce baselines on the **same hardware/harness** and at the **same `DEVICE_BATCH_SIZE`**;
  state caveats and compare **like-for-like** (a published 0.9344 at native batch is not your
  0.9344 at a different micro-batch).

---

## 3. The validity gate is the SEED-NOISE floor — the discipline that wins or wastes the run

This is the section that separates a fixed-budget LM run from a kernel task, and where the live
run bled the most hours. There is no t-test handed to you — **establishing the noise floor is
YOUR job**, and every keep/reject decision is a hypothesis test against it.

- **Respect the five-minute cost.** Every official candidate consumes 300 seconds of training
  plus compile/evaluation overhead. Default ordinary candidate screens to **ONE clean run**.
  Do not spend 3/5/10 seeds proving every mediocre idea to death.
- **Calibrate noise once, cheaply.** Run the retained baseline at N=3 seeds and compute an initial
  σ_seed before optimizing. Extend to N=5 only when N=3 is unstable or a final/SOTA claim needs
  tighter uncertainty. Keep same-seed fresh-process variance separate from cross-seed variance.
- **Gate every keep/reject at 2-3 σ_seed.** If `|floor − mean|` is smaller than that, the call is
  a **coin flip** — re-screen at higher N before banking, or do not bank it. Banking sub-σ
  "wins" builds a floor out of lucky seeds.
- **Classify every result: REAL signal vs SUB-NOISE jitter.** A −0.0007 "improvement" with
  σ_seed≈0.001+ is jitter; a −0.009 jump is real. Only the latter banks.
- **Multi-seed-confirm only SOTA-level candidates.** Reserve the expensive confirmation for a
  mechanism-credible candidate that clears the floor by a clear margin AND is near the target;
  start small and extend only if the decision genuinely depends on tighter uncertainty.
- **Be confidently selective.** A diagnosis-backed hypothesis plus one clean real run is enough
  to discard a regression and choose the next direction. Confidence means making the research
  decision instead of hedging with repetitive seeds; it never means promoting a sub-noise tie.
- **Bank the floor only on a REAL win, and then STACK.** Maintain a current-best `train.py`; add
  each REAL lever onto it and co-tune. Revert only a lever that measurably (>σ) hurts. A run that
  reverts to bare vanilla after every experiment can never reach a stacked frontier.

> Worked failure (`NanoChat Autoresearch Hands-on Trace`): the live run banked a 0.001860 over a
> 0.002580 (Δ=0.0007) and rejected 0.002330 / 0.002640 — **all on 0.0002–0.0008 deltas, below any
> plausible σ_seed**. ~25 candidates / ~5 hours shuffled inside a ~1.002–1.005 band that was
> statistically a single point. The fix was never "a better trick" — it was a noise gate.

---

## 4. Diagnosis: where does the budget go?

Optimizing the non-bottleneck is the #1 way to waste a day. Decompose first, with real
measurement — you have **direct GPU access** (`ssh -p 2231`), so use it.

- **Budget decomposition:** `effective_steps ≈ (300 − warmup/compile) / per_step`. Know how many
  steps/tokens the budget actually buys before touching anything.
- **The convergence curve (the most important plot):** `val/train loss` vs step, and **where on
  it 300s lands.** A from-scratch run in 300s is usually **far from converged** — the curve is
  still steep at the budget cutoff. That means BOTH levers are open: more steps (throughput) AND a
  steeper curve (sample-efficiency) both lower the endpoint. (Contrast a near-converged regime,
  where only sample-efficiency helps.)
- **Per-step profile:** decompose a steady-state step (after compile) into forward / backward /
  optimizer update / data load. On B200 the hardware counters are blocked (`ERR_NVGPUCTRPERM`),
  so use `torch.profiler` + timing + a roofline estimate; attribute per-step to **one** dominant
  term before optimizing it (a faster step = more steps = lower bpb).
- **Reference ceilings (carry them always, re-measured in YOUR harness units):** the vanilla
  baseline at the scorer's `DEVICE_BATCH_SIZE`; the named anchors (reference optimized, frontier
  best); and a quick `torch.compile`/throughput read so you know the per-step floor.
- Attribute the gap to **one** cause (too-few-tokens vs too-low-quality-per-token vs a specific
  per-step term) before choosing a move. "It's throughput *or* quality" is a measurement you
  skipped.

---

## 5. The bottleneck taxonomy (which wall?)

| Wall | What binds | Tell (from the decomposition) | First move |
|---|---|---|---|
| **Sample-efficiency-bound** | loss-per-token too high; curve too shallow | steep curve at 300s, still far from a plateau; more steps would clearly help | optimizer, capacity allocation, init/reg/schedule — the big lever |
| **Throughput-bound** | too few tokens fit in 300s | per-step cost dominates; obvious compute/attention/MLP hot term; curve would drop with more steps | windowed/local attention, leaner MLP, precision, fused kernels — buys steps |
| **Capacity-misallocation-bound** | params in the wrong place (too deep/narrow, weak head) | reshapes (depth↔width, bottleneck, head) move the floor while same-param tweaks don't | reallocate capacity — depth/width/bottleneck/head, **co-tuned** (§7) |
| **Optimization-bound** | update rule leaving quality on the table | optimizer swap moves the floor a lot (e.g. Muon ≫ Adam here) | the optimizer family + its momentum/orthogonalization/decoupling |
| **Numerics-stability-bound** | low precision / aggressive scaling diverges or wastes the budget | loss spikes / NaNs / a precision trick is net-negative | safer scaling, QK/value norm, logit/residual shaping, or back off |

The one people miss: **sample-efficiency-bound looks like "the model is just not good enough"** —
but the lever is the optimizer + how capacity is allocated + init/schedule, not a bigger model or
a thrown trick. If 300s only buys a few thousand steps and you only ever tweak one scalar, you
are polishing the small lever forever.

---

## 6. The optimization toolkit, ordered by LEVERAGE

Apply in this order. The biggest wins are at the top; the per-token micro-tweaks at the bottom
are where the night gets burned if you start there. **Ask "what is the lowest bpb this budget can
reach, and which lever class moves it most?" before "what trick can I add?"**

1. **Optimizer (the biggest single lever for fixed-budget from-scratch LM).**
   Newton-Schulz-orthogonalized momentum (Muon) on the matrices is the canonical large jump here;
   the Muon/Adam split across parameter banks (embeddings, head, scalars), momentum and
   orthogonalization budget, and the LR coupling are all live. **Most replacements of a working
   Muon regress** — it is the strong base; tune it, don't swap it out.
2. **Capacity allocation at ~fixed params — and CO-TUNED (this is the structural lever).**
   Where the parameters sit: depth vs width, a narrow trunk bottleneck, the width of the output
   path, residual/layer structure. This is the most productive architecture axis AND the one that
   breaks greedy search: **its pieces regress in isolation and pay off together** (§7, §9). Reshape
   as a co-designed bundle, not one dial.
3. **Effective-update numerics — init/residual scaling, schedule, loss/logit shaping.**
   Depth-matched residual/init scaling, the warmup/decay schedule, and output-logit shaping are
   frequently HIGH-leverage and frequently left unexplored. (In the live run, residual-init scaling
   and logit shaping were two of the biggest late jumps and sat untouched for ~30 candidates.)
4. **Normalization / residual numerics.** Norm placement (pre/post/sandwich), QK/value
   normalization — real but usually smaller, and easy to mistake noise for signal here.
5. **Data order / curriculum.** Shard ordering, packing, doc boundaries — real but typically
   saturates near one bit of improvement.
6. **Throughput (lever A) — windowed/local attention, leaner numerics — as STEP-BUDGET.** These
   buy more steps in 300s; treat the bought steps as quality and re-spend them on levers 1–3.

**The leverage rule, stated once:** an optimizer/allocation win multiplies against every token
the budget buys; a single-scalar tweak near a saturated knob is noise. When unsure which to do
next, do the optimizer-or-allocation experiment, not the fourth norm variant.

---

## 7. Optimizer / architecture prior library (the priors)

Internalize these so the curve *confirms* rather than *discovers* — and so you recognize the
regime without re-deriving it:

- **The tier sets the regime.** A small GPT trained from scratch for only 300s is
  **sample-efficiency-bound and FAR from converged**: the budget buys few passes, so both
  "more effective tokens" and "steeper curve" lower the endpoint, and the leaderboard delta is
  dominated by the optimizer + capacity allocation + init/schedule, not by adding a single trick.
- **Muon / Newton-Schulz orthogonalized momentum** is the strong base on the matrices here;
  splitting Muon vs Adam across banks and tuning the orthogonalization/momentum is where optimizer
  gains live. Treat all-X-optimizer replacements as likely regressions.
- **Capacity allocation is a CO-TUNED point, not a dial.** A narrow trunk underfits ALONE; a wider
  output path ALONE is uncoordinated; depth↔width reshapes interact with init/residual scaling and
  the schedule. The frontier reshapes several of these **together**. This is precisely why greedy
  single-lever search stalls (§9): the productive region of the space is a joint optimum whose
  axes regress individually.
- **Init / residual scaling matched to depth, and logit/output-head shaping**, are high-leverage and
  cheap to try — and easy to leave for candidate #47 by accident. Reach them early.
- **Normalization placement and QK/value norm** are real but small; gate them hard against σ_seed.
- **Throughput levers (windowed/local attention, leaner numerics)** are step-budget, not quality
  knobs — bank the steps, re-spend on the big levers.

A senior researcher reads "small GPT from scratch, 300s on one B200, scored by mean val_bpb,
vanilla ≈1.05, reference optimized ≈0.93" and *already knows*: this is **sample-efficiency-bound,
far from converged**; the ~0.12 gap is an **optimizer + a co-designed capacity reallocation +
matched init/schedule/head shaping**, NOT a single trick or a bigger model; and the last leg is a
**coordinated bundle whose pieces regress alone**. The decomposition then *quantifies* the gap and
the σ floor; it does not reveal the regime. **The specific co-design that realizes it is what you
derive on the box — this guide does not hand it to you.**

---

## 8. Operator / lever → canonical move

| Symptom (from §4) | Binding lever | Canonical move |
|---|---|---|
| steep curve at 300s, few steps bought, far from plateau | sample-efficiency | optimizer + capacity allocation + init/schedule — the big lever |
| per-step compute/attention/MLP dominates; more steps would clearly help | throughput | windowed/local attention, leaner MLP, precision — buys steps, then re-spend |
| reshapes move the floor while same-param scalar tweaks don't | capacity allocation | reshape depth/width/bottleneck/head **as a co-tuned bundle**, then ablate within |
| optimizer swap moves the floor a lot | optimization | tune Muon (split/momentum/orthogonalization); don't replace it wholesale |
| a piece REGRESSES alone but is plausibly structural | synergy | **retry it INSIDE a bundle**, do not discard it (the greedy trap) |
| `|floor − mean|` smaller than σ_seed | **seed noise** | **do NOT bank — re-screen at higher N or hold**; it's a coin flip |
| many single-knob screens, floor not moving | process | stop nibbling; measure σ, work the big lever, search BUNDLES |

---

## 9. The experimental discipline + the BUNDLE search (the non-greedy move)

Run the search like an experimentalist — but recognize that **greedy single-variable search has a
known failure mode on this task** and plan around it.

- **Measure σ_seed first; gate every keep/reject at 2-3σ** (§3). A sub-noise decision is not an
  experiment, it is a coin flip.
- **One variable per experiment for ATTRIBUTION — but the unit of PROGRESS is a co-designed
  bundle.** Because the frontier's pieces regress in isolation, a pure greedy "one category vs the
  floor" search rejects each piece and never assembles the combination. The fix:
  - propose a **2–4 lever bundle as ONE candidate**, motivated by a structural hypothesis
    ("reshape the trunk AND the head AND match the init/residual scaling for the new shape");
  - screen the **bundle**; if it wins, **ablate WITHIN it** (one-variable-off) to find who carries
    the gain and what is dead weight;
  - keep a **synergy-shortlist**: any lever that regressed ALONE but is plausibly synergistic gets
    retried inside a bundle, not discarded.
- **Always carry the reference ceilings** (vanilla, reference optimized, frontier best — all
  re-measured at the scorer's `DEVICE_BATCH_SIZE`) so you know absolute σ-honest distance, not just
  relative deltas.
- **Stack, don't revert** (§3, §6).
- **Know the irreducible floor and stop there.** When the remaining gap needs a coordinated
  structure you have not co-tuned, recognize the gap is the frontier, not your last knob, and say
  so honestly with the σ-honest distance — instead of grinding noise inside a saturated band.
- **The chain:** validity (real FA-4, frozen scorer, σ-gate) → decomposition (budget split,
  curve-at-300s) → one lever class OR one bundle → re-measure → attribute.

Record each candidate as the **measured causal chain** (see `NanoChat Autoresearch Hands-on Trace`
for a filled example):

```text
CAND <name, on top of which floor, lever CLASS(es)>
  measured:    val_bpb per-seed; mean ± sd over N (scorer, real FA-4, sequential)
  noise-gate:  (floor − mean) vs 2-3·σ_seed -> REAL | SUB-NOISE
  decompose:   effective steps/tokens in 300s; where 300s lands on the curve; throughput vs quality
  read:        better/worse by how many σ? if a regression, wrong lever or right-but-synergistic?
  attribution: which class moved (optimizer / allocation / numerics / norm / data / throughput)
  decision:    bank & stack | keep-in-stack & co-tune | revert | hold-for-bundle — by the rule
  → next:      the next coordinated change, by the binding constraint
```

Lightweight persisted artifacts (for the multi-round harness): `research/GROUND_TRUTH.md`
(scorer command, hardware, `DEVICE_BATCH_SIZE`, budget, val shard, the σ_seed floor, re-measured
anchors, the active floor), `research/PROFILE.md` (per-step split + curve-at-300s),
`attempts/<cand>/CHANGES.md` (the diff + one-line hypothesis), `RESULTS.md` (table ranked by
mean `val_bpb`, honest about REAL-vs-SUB-NOISE for every near-miss).

---

## 10. Worked example (compact)

NanoChat Task 1 on one B200, **minimize mean `val_bpb` in 300s**, frozen
`./eval_solution.sh train.py <N>` (real FA-4, `DEVICE_BATCH_SIZE=64`, sequential seeds,
`shard_06542` val). Vanilla re-measured ≈ 1.053 (ref 1.0587); anchors: reference optimized 0.9344,
best 0.9109. Regime read up front: **sample-efficiency-bound, far from converged, ~0.12 gap =
optimizer + co-designed allocation + matched init/schedule.**

```text
CAND muon_optimizer — optimizer class, on vanilla
  measured:    mean val_bpb 1.028 (vs floor 1.053)  -> Δ −0.025, REAL (far above σ_seed)
  decompose:   same step budget; quality-per-token improved (steeper curve) — sample-efficiency lever
  read:        large REAL win; optimizer is the binding lever, exactly the §7 prior
  attribution: optimizer (Newton-Schulz orthogonalized momentum) — the canonical first jump
  decision:    BANK as floor; KEEP IN STACK; co-tune the next class (capacity allocation) onto it
  → next:      reshape capacity AS A BUNDLE (trunk + head + matched init), not one dial (§9)
```

```text
CAND deep_narrow_arch (alone) — capacity allocation, on the stack
  measured:    mean val_bpb ~+0.03 worse than floor  -> REGRESSION
  read:        a narrow trunk ALONE underfits — but allocation is a CO-TUNED optimum (§7)
  attribution: right lever CLASS, wrong because tested in ISOLATION
  decision:    do NOT discard — HOLD on the synergy-shortlist, retry inside a {trunk+head+init} bundle
  → next:      build the bundle; screen the bundle; ablate within the winner
```

This is the **noise-gated, bundle-aware** loop: the optimizer win banks because it clears σ; the
isolated reshape is held-not-discarded because allocation is synergistic. A fuller failure-first
version is in `NanoChat Autoresearch Hands-on Trace`; **derive your own frontier from your numbers
— this guide does not contain it.**

---

## 11. Senior-level mistakes (sharper than "common pitfalls")

- **Deciding below the noise floor:** banking/rejecting on `val_bpb` deltas smaller than σ_seed
  (never measured). The fix is a σ gate, not a better trick.
- **Greedy single-lever search on a synergistic frontier:** testing co-tuned pieces in isolation,
  rejecting each because it regresses alone, never reaching the bundle. Search bundles; hold
  regressed-alone levers for retry.
- **Reaching the high-leverage lever (init/residual scaling, logit/head shaping) by luck on
  candidate #47** instead of ordering the classes by leverage up front.
- **Treating throughput levers as quality knobs** instead of step-budget under a fixed clock.
- **Swapping out a working Muon** (all-Lion/Sophia/etc.) — usually a regression; tune it instead.
- **Comparing across contention levels / `DEVICE_BATCH_SIZE`** — a contended or wrong-batch number
  is a different measurement; never bank or compare it.
- **Any environment drift** (SDPA fallback, faked kernels, FA2, wrong lib.py/data) — the score is
  INVALID, not a slightly-different win.
- **Letting the harness masquerade as the model:** a flaky launcher or a frozen reviewer stalling a
  mission read as "the model failed." Read the real error; fix the harness first.
- **Grinding a saturated band** (or re-screening noise) instead of working the big lever or
  stopping honestly when the gap is the coordinated-structure frontier.
- **Reproducing or leaking a reference recipe** instead of deriving and measuring your own.

## Response shape
- Target metric, scorer command, hardware, `DEVICE_BATCH_SIZE`, the budget, the vanilla
  re-measured baseline, the measured σ_seed, and the named anchors (0.9344 / 0.9109) in your units.
- The dual decomposition (budget split; where 300s lands on the curve; throughput vs quality) and
  the bottleneck wall, with evidence.
- The measured causal chain per candidate (scorer numbers only), each result labeled REAL or
  SUB-NOISE; regressions labeled wrong-lever vs right-but-synergistic.
- σ-honest absolute distance to the next anchor, and whether closing it needs the big lever, a
  co-designed bundle, or a real invention you don't yet have.
- The next lever class or bundle chosen by the binding constraint, with the exact scorer command —
  and never the reference recipe itself.
