---
name: Speedrun SOTA Optimization
description: A senior-researcher methodology for training-speedrun benchmarks (modded-nanogpt / NanoGPT-speedrun and kin) — minimize wall-clock to a fixed quality target under a statistical validity gate. Opens with a research-first discipline (you are knowledge-limited: retrieve, reproduce, and corroborate the concrete prior art before you build — invention is recombination, and frontier agents measurably fail to re-implement known gains) and a named technique menu to search for. Then: the training run as steps x per-step-cost, the bottleneck taxonomy (convergence-bound vs step-cost-bound vs comms-bound vs precision-stability-bound), where-does-the-wall-clock-go diagnosis, the statistical-validity discipline (N=3 iterate / N=10 certify / bank-the-floor / stack-don't-revert / quality-vs-power), an optimization toolkit ordered by leverage (optimizer & schedule first, kernels & precision last), the optimizer/architecture prior library, and the experimental discipline. Distilled human expertise to learn, not a recipe to copy.
category: benchmark-training-speedrun
version: 2
created_at: 2026-06-18T00:00:00+00:00
---

## Title
Speedrun SOTA Optimization

## What this is
This is **distilled human expertise** — how a senior ML-systems researcher actually
thinks about a training-speedrun, not a checklist. Read it to acquire the mental model
and the priors; do not treat it as steps to mechanically execute or a recipe to
transplant. The numbers and worked references exist to teach the *method*; the method
generalizes across speedrun tiers and metrics. When a real run teaches you something
sharper, evolve this. (Companion: `Speedrun Hands-on Trace` is one fully-worked,
failure-first example of the loop.)

## When to use
- The objective is to **minimize wall-clock training time** to reach a **fixed quality
  target** (e.g. `val_loss <= 3.28`, `val_bpb <= X`) on real GPUs (8xH100 / B200), with a
  frozen scorer and a **statistical validity gate** (a one-sided t-test over N runs).
- The task names an editable recipe (`train.py` + kernels), a frozen scorer, and a numeric
  wall-clock metric — modded-nanogpt / nanochat-style speedruns and their kin.

## When NOT to use
- A single GPU kernel (use `SOL Kernel SOTA Optimization`), a paper benchmark matrix, or an
  RL/post-training run.
- The scorer is missing and cannot be reconstructed — write a setup/blocker report first;
  do not invent a metric or a `p`-value.

---

## 0. Research-first: you are knowledge-limited — RETRIEVE before you build

This is the section the agent most needs and most skips. A senior researcher's first move
is **not** to think harder; it is to **go read what humans already figured out**. An LLM
agent has a sharper reason to: your knowledge is *parametric* — frozen at cutoff, capacity-
bounded, weakest exactly on the long-tail/post-cutoff facts that matter here (new optimizers,
new kernels, the SOTA recipe's rationale, hardware-specific tricks), and you are trained to
sound confident, so from memory you will emit plausible-but-wrong technique facts ("X gives
2×"). The evidence is blunt: on the **Automated LLM Speedrunning Benchmark** (these very
NanoGPT records) frontier agents largely *cannot re-implement even the next record's gains*
when handed the prior record's code plus pseudocode hints; **FIRE-Bench** finds frontier
research agents stay under ~50 F1, failing at **experimental design + evidence-grounding**,
not at writing the kernel. The binding skill is **retrieve → reproduce → verify**, not raw
cleverness.

**Invention is recombination, so retrieve and stack — do not try to invent from scratch.**
Bibliometrics across tens of millions of papers (Nature 2022, *atypical combinations*) show
genuinely unprecedented ideas are rare; almost all progress is *novel re-mixing of known,
validated parts*. Every speedrun record is the prior record's code **plus a few small,
orthogonal, already-published gains** stacked on top. So your job is NOT a never-seen
optimizer; it is to aggressively retrieve the menu of known levers, stack the orthogonal
ones, re-implement each carefully (the step agents fail), and measure. Reciting a known
trick from memory and calling it "my invention" is the classic agent failure that
literature-grounded novelty checkers exist to catch — and pulling a technique from memory
instead of from its concrete artifact is how you get the coefficients/conditions wrong.

**The discipline (search-then-build — the order real end-to-end research systems use):**
1. **Reproduce first.** Stand up the baseline AND the named anchor on *your* hardware and
   re-measure like-for-like before trying to beat anything. Reproduction is research, not setup.
2. **Chase the most concrete artifact.** pseudocode/repo/commit-diff/reference-kernel/ablation-
   table > prose > abstract. Reasoning about a technique in the abstract is where agents flail;
   go get its executable form and read it.
3. **Corroborate, don't trust one blog.** A single "N× speedup" is a parametric-quality claim
   until a second source confirms it; treat headline multipliers as **upper bounds under
   unequal tuning** (*Fantastic Pretraining Optimizers*, 2509.02046: matrix optimizers are only
   ~1.1–1.4× and shrink toward ~1.1× at ~1.2B — and tune the BASELINE as hard as the new thing).
4. **One lever, measured against retrain variance, then stack.** Adopt a change only if it
   clears run-to-run variance (§3); then add the next orthogonal lever onto the best stack.

**The anti-cheat line (non-negotiable):** retrieve GENERAL technique, methodology, and design
*rationale*. NEVER retrieve this task's leaderboard answer / best-solution code. Understanding
*why* a component exists is research; copying the answer key is disqualifying.

**The menu of human knowledge to retrieve (named, so you can search for it):**

| Lever | Technique (search the arxiv/repo, don't recite) | Axis |
|---|---|---|
| optimizer / fewer steps | **Muon** (orthogonalized momentum, matmul-only Newton-Schulz) + **Moonlight** (RMS-match to AdamW + decoupled WD to scale it); **SOAP** (Adam in Shampoo eigenbasis, eigendecomp every k); **Sophia** (diagonal-Hessian + clipping) | convergence |
| LR / horizon | **WSD** (warmup-stable-decay: long plateau, short cooldown — cash out a converged ckpt at any budget); **muP/muTransfer** (tune LR on a small proxy, transfer zero-shot — a meta-speedup for your tuning budget) | convergence + tuning cost |
| batch | **critical batch size** + **batch-size warmup** (below CBS bigger batch ≈ linear step savings; above it wastes tokens) | steps ↔ per-step |
| regularization | annealed **Langevin/SGLD** gradient noise (generalization-per-step) | convergence |
| data | **RHO-Loss** (online reducible-holdout batch selection — skip already-learned/noise), **DoReMi** (proxy-tuned data mixture), **sequence-length warmup** (cuts early attention FLOPs AND stabilizes) | convergence, ~free |
| per-step cost | **FA3**/fused kernels; **torch.compile** fusion + **CUDA graphs** (launch-bound); **FP8 GEMMs** with delayed scaling (watch SwiGLU outlier loss spikes) | per-step |
| variance / the metric | **LAWA / weight-averaging / EMA** denoise the iterate → lower run-to-run variance → certify with the mean rode closer to the gate = fewer steps. (Classic **SVRG is ineffective** in DL; momentum + larger batch + averaging are the variance reduction that works.) | the t-test lever |

**Search playbook (general technique only — never the answer key):** the arxiv/repo for each
lever above; *Fantastic Pretraining Optimizers* before believing any optimizer multiplier; the
diagnostic method (roofline, MFU ≈ achieved/peak with ~6N FLOPs/token, nsys-then-ncu,
torch.profiler op attribution) so you self-measure the bottleneck; and a literature-grounded
novelty check before you ever call something "new".

---

## 1. The mental model: wall-clock is steps × per-step-cost

A speedrun's wall-clock is, to first order:

```text
train_time ≈ counted_steps × per_step_cost  (+ fixed costs: compile, warmup)
```

There are exactly **two levers**, and they are not equal:

- **Fewer steps to the target** — a *convergence* improvement: the loss curve reaches the
  target in fewer optimizer steps. Lives in the optimizer, the LR/schedule, init, data
  order, batch/seq sizing, architecture. **This is the 10x lever and it compounds**: a
  recipe that needs 1200 steps instead of 1385 is 13% faster *and* every per-step saving
  multiplies against fewer steps.
- **Cheaper per step** — a *throughput* improvement: each step costs less (faster attention,
  leaner MLP, FP8 GEMMs, comms overlap, fused kernels). Bounded by the **hardware roofline**:
  once a step is near peak, there is little left, and precision tricks carry structural
  overhead.

A training run, like a kernel, **is bottlenecked by one of these and wastes effort spent on
the other.** The whole craft is: find which one binds, attack it, re-measure.

> The single most common senior-level error on a speedrun is spending the day on per-step
> kernel/precision micro-opts (fun, visible, roofline-bounded) while the **convergence**
> lever — which is where the leaderboards are actually won — goes untouched. Recursive's
> 77.3s frontier on Task 2 was **five stacked inventions**, most of them convergence/optimizer
> changes (annealed SGLD noise in NorMuon, sign-agreement Adam on the bigram/value banks,
> schedule retunes), with only one leaner-MLP kernel. Lever (a) first.

---

## 2. Honesty rules (non-negotiable, the only hard floor)

- The **frozen scorer** is the only source of truth for `train_time`, `val_loss`, and
  validity. A self-timed number is a hypothesis, not a result.
- **The score is the VERIFIER's re-measured `train_time`, mean over N runs, for a VALID
  candidate only.** A faster run that fails the t-test is **INVALID, not a win** — never
  bank it, never report it as the result.
- **No fabricated numbers.** `p(mean<target)` comes from the frozen `analyze_sweep.py`
  t-test over the actual N runs — never eyeballed from one run, never nudged. `train_time`
  comes from the recipe's counted timer as the scorer re-runs it.
- **Do not game the gate.** Never special-case the val set, hardcode outputs, shortcut
  training, alter the val data / loss / target / t-test / scorer, or touch the timer. The
  val data and metric are frozen precisely so you cannot.
- Reproduce baselines on the **same hardware/harness**; state hardware/measurement caveats
  (an "official 79.7s" on other hardware is not your 79.7s) and compare **like-for-like**.

---

## 3. The validity gate is STATISTICAL — the discipline that wins or wastes the run

This is the section that separates a speedrun from a kernel task. Validity is
`p(mean<target) < α` from a one-sided t-test over N runs — a function of the **mean, the
run-to-run sd, AND N**. Internalize:

- **Iterate at small N (3) for signal; certify at large N (>=10).** N=3 is a probe, not a
  verdict.
- **Classify every INVALID: QUALITY miss vs POWER miss.**
  - *Quality miss*: `mean > target` (or barely under with large sd). The recipe genuinely
    isn't good enough — engineer convergence/quality.
  - *Power miss*: `mean << target` with small sd, but `p` just over α (e.g. mean 3.2765 vs
    target 3.28, p=0.013 at N=3). **The recipe is already good; only N is too small.** `p`
    shrinks with N at fixed mean/sd, so a mean clearly inside almost always certifies at N=10.
- **The cardinal sin:** treating a power miss as a quality miss and re-engineering a recipe
  you have already won. The fix for a power miss is **more seeds**, full stop.
- **Bank the floor the moment you have a clearly-valid faster candidate** — N=10-certify and
  move the global best to it. Do not keep "improving" validity you already hold.
- **Stack, don't revert.** Maintain a *current-best recipe*; add each promising mechanism
  onto it and co-tune. Revert only a mechanism that *measurably* hurts. A run that reverts to
  the bare floor after every experiment can never reach a stacked frontier.

> Worked failure (`Speedrun Hands-on Trace`): a 79.55s candidate with `val_loss=3.2771`
> (clearly < 3.28) read INVALID at `p=0.085` (N=3). It was a *power* miss. The run spent ~6
> missions / 3 hours building further mechanisms; the same hybrid recipe certified VALID at
> N=10 (`p=0.004`, 79.77s) with **zero recipe change**. The win was in hand for hours.

---

## 4. Diagnosis: where does the wall-clock go?

Optimizing the non-bottleneck is the #1 way to waste a day. Decompose first, with real
measurement — you have **direct GPU access** (`ssh h100` / the run box), so use it.

- **Step decomposition:** `per_step = train_time / steps`. Know it before touching kernels.
- **The convergence curve (the most important plot):** `val_loss` vs step. *Where* does it
  cross the target? If it crosses only in the final few % (e.g. val_loss@step1250 ≈ 3.331 for
  a 1385-step run targeting 3.28), **naive step cuts are off the table** — the end of the
  curve has no slack, and you must improve convergence to cut steps. If it crosses early with
  a long flat tail, the tail is wasted steps — cut or anneal them.
- **Per-step profile:** decompose a steady-state step (after compilation) into forward /
  backward / optimizer update / **NCCL comms (reduce/gather)** / data load. Use
  `torch.profiler` or CUDA events; on 8x, NCCL all-reduce and the optimizer's matrix work
  (Muon/NorMuon `XTX`/`XXT`/polar) are frequent hidden costs. Attribute the per-step cost to
  **one** dominant term before optimizing it.
- **The reference ceilings (carry them always):**
  - run `torch.compile` / the seed as-is to know the *absolute* step-cost floor on this box;
  - measure NCCL bandwidth and FA3 cost so you know what is comms vs compute;
  - know the *named anchors* (seed, public record, automated frontier) in **your** harness
    units, re-measured — not their published numbers.
- Attribute the gap to **one** cause (convergence vs a named per-step term) before choosing a
  move. "It's steps *or* per-step" is a measurement you skipped.

---

## 5. The bottleneck taxonomy (which wall?)

| Wall | What binds | Tell (from the decomposition) | First move |
|---|---|---|---|
| **Convergence-bound** | needs too many steps to hit target | curve crosses target only at the very end; long tail at high loss | optimizer (noise/regularization), schedule, init, data order — the big lever |
| **Step-cost: compute-bound** | forward/backward FLOPs dominate per step | per-step ~flat in batch; GEMM/attention is the hot term | FA3/attention impl, leaner MLP, tensor-core precision, fused kernels |
| **Step-cost: comms-bound** | NCCL reduce/gather on 8x | per-step rises with world size; reduce/gather a large slice of the step | overlap comms with compute, bucket/all-reduce tuning, lower-precision grads |
| **Step-cost: optimizer-bound** | Muon/NorMuon matrix work per step | optimizer update is a large per-step slice | fuse/precision the optimizer kernels; cheaper polar/orthogonalization |
| **Precision-stability-bound** | low precision diverges or needs costly scaling | FP8/FP6 runs blow up or need per-step amax that eats the win | delayed/cached/per-block scaling, partial coverage, or back off precision |
| **Fixed-overhead-bound** | compile/warmup is a big fraction | short runs; wall >> steps×per-step | amortize compile, fewer recompiles, CUDA graphs |

The one people miss on a speedrun: **convergence-bound looks like "the recipe is just slow"**
— but the lever is the optimizer/schedule, not the kernels. If the curve needs 1385 steps and
you only ever shave per-step cost, you are polishing the small lever forever.

---

## 6. The optimization toolkit, ordered by LEVERAGE

Apply in this order. The biggest wins are at the top; the per-step micro-opts at the bottom
are where the night gets burned if you start there. **Ask "what is the minimum number of
steps × the minimum per-step cost this target requires?" before "how do I make this kernel
faster?"**

1. **Reduce steps-to-target — convergence (10x lever, compounds).**
   The optimizer and its regularization (Muon/NorMuon; injected noise — annealed SGLD/Langevin;
   sign-agreement / sign-trick updates on specific banks); the LR/warmup/cooldown schedule and
   its retunes; init; batch/sequence sizing; data ordering/curriculum; architecture levers that
   aid optimization (softcap, embedding splits, residual scaling). Most leaderboard deltas live
   here. Measure on the **convergence curve**, and always re-check the target is still hit.
2. **Reduce per-step cost without hurting convergence.**
   Better attention impl (FA3); a leaner forward (the post-only / fused ReLU² MLP that stores
   fewer activations); kernel fusion to cut HBM round-trips and launches; **comms overlap**
   (start NCCL on ready grads while compute continues). These are real but bounded by roofline.
3. **Precision as a lever (within stability — it is a dial inside a gate, not free).**
   FP8/FP6 GEMMs on projections — but a per-step/per-block **scale is structural overhead**, so
   FP8 is net-positive only when the scaling is **delayed/cached/fused** and the op is the
   bottleneck. Naive fixed-scale diverges; dynamic per-step amax often costs more than the GEMM
   saves. Expect to *fight* for net-speedup; fix layout/scaling first, and report net-slower as
   net-slower. (See `Speedrun Hands-on Trace`, nail 3.)
4. **Schedule / step-count tuning — LAST, and only after convergence is improved.**
   Cutting or annealing steps is valid only if the curve has tail slack or a convergence win
   created it; a naive step cut on a tight curve just fails the gate. Verify on the curve, not
   by hoping.

**The leverage rule, stated once:** a convergence win multiplies against every per-step win;
a per-step win is capped by the roofline. When unsure which to do next, do the convergence
experiment.

---

## 7. Optimizer / architecture prior library (the priors)

Internalize these so the curve *confirms* rather than *discovers*:

- **The tier sets the regime.** GPT-2-small-class modded-nanogpt to a fixed val_loss on 8xH100
  is **convergence-bound near the end**: the seed already uses FA3 + a strong optimizer
  (Muon/NorMuon) + FP8 GEMMs + fused Triton + a multi-stage schedule + ~1385 steps, and the loss
  crosses target only in the last ~10%. So per-step cost is already near-roofline; the open lever
  is *fewer steps via better optimization*, not cheaper steps.
- **Muon / NorMuon** orthogonalize the update (polar/Newton-Schulz `XTX`/`XXT`); they are strong
  but their matrix work is a per-step cost and their **regularization is tunable** (injected
  noise, momentum, the orthogonalization budget). Convergence gains and step-cost both live here.
- **Injected-noise regularization (SGLD/Langevin, annealed)** can improve generalization-per-step
  → fewer steps to target. A known frontier move; cheap to try, measured on the curve.
- **Sign-agreement / sign-trick updates** on specific parameter banks (bigram/value/embedding)
  are a known lever for these recipes (#83 itself is "Sign Trick on Bigram Embed").
- **FP8 on 8xH100 (Hopper, E4M3/E5M2)** needs scaling; delayed/per-tensor scaling amortizes the
  amax. The attention projections and MLP GEMMs are the candidates; the MLP activation-storage
  path is where a leaner-kernel win lives. Treat as the small, structural lever.
- **NCCL on 8x** — reduce/gather can be a large step fraction; overlap with compute and consider
  lower-precision gradient comms within the stability budget.

A senior researcher reads "GPT-2-small to val_loss 3.28, seed already FA3+NorMuon+FP8, curve
crosses 3.28 only at step ~1350/1385" and *already knows*: this is **convergence-bound at the
tail**, the leaderboard delta is an optimizer/noise/schedule change that pulls the crossing
earlier, and the per-step kernels are a secondary 1–2% game. The decomposition then *quantifies*
the gap; it does not reveal the regime.

---

## 8. Operator / lever → canonical move

| Symptom (from §4) | Binding lever | Canonical winning move |
|---|---|---|
| curve crosses target only at the end; need fewer steps | convergence | optimizer noise/regularization, schedule retune, init, data order — re-measure the curve |
| optimizer update is a big per-step slice | optimizer step-cost | fuse/precision the polar/orthogonalization kernels |
| NCCL reduce/gather large on 8x | comms | overlap comms with compute; lower-precision grads within tolerance |
| MLP/attention GEMM is the hot per-step term | compute | FA3, leaner fused MLP, tensor-core precision with scaling |
| FP8 candidate diverges or is net-slower | precision-stability | delayed/cached/per-block scale, partial coverage, or back off |
| a faster candidate, mean clearly < target, p just over | **statistical power** | **N=10 certify and BANK — not another mechanism** |
| many mechanisms tried, floor not moving | process | stop nibbling; stack onto best, bank the near-win, work the big lever |

---

## 9. The experimental discipline + the measured causal chain

Run the search like an experimentalist:

- **One variable per experiment.** Attribute the gap to one cause (convergence vs a named
  per-step term) before the next move.
- **Always carry the reference ceilings** (seed, public record, automated frontier — all
  re-measured on your harness) so you know absolute distance to the limit, not just relative
  deltas.
- **Classify every miss (quality vs power) and bank every clearly-valid near-win at N=10**
  before doing more mechanism work (§3).
- **Stack, don't revert** (§3, §6).
- **Know the irreducible floor and stop there.** A recipe at the roofline on per-step with a
  tight convergence curve, a gap to the next anchor that needs an invention you don't have —
  recognize when the remaining gap is the frontier, not your code, and say so honestly instead
  of grinding noise or re-certifying validity you already hold.
- **Correctness/validity (the frozen t-test) → decomposition → one mechanism → re-measure →
  attribute.**

Record each candidate as the **measured causal chain** (see `Speedrun Hands-on Trace` for a
filled example):

```text
CAND <name, on top of which floor>
  measured:    train_time (scorer, N), val_loss (scorer), p(mean<target) (frozen t-test) -> VALID/INVALID
  decompose:   steps × per_step; convergence: val_loss@mid-step
  read:        faster/slower? quality in or out? if INVALID -> QUALITY or POWER miss?
  attribution: which lever moved (convergence / per-step term / precision-stability), from the numbers
  decision:    bank N=10 / stack & co-tune / revert / abandon — by the rule
  → next:      the next mechanism, chosen by the binding constraint
```

Lightweight persisted artifacts (for the multi-round harness, not the research itself):
`research/GROUND_TRUTH.md` (scorer command, hardware, target, t-test rule, decomposition,
re-measured anchors), `research/PROFILE.md` (per-step breakdown + convergence curve),
`experiments/<candidate>/RESULT.md` (the chain block + raw `SCORE` log),
`RESULTS.md` (final table ranked by `train_time` among VALID candidates, honest about the
power-vs-quality status of every near-miss).

---

## 10. Worked example (compact)

NanoGPT-speedrun (Task 2) on 8xH100, target `val_loss <= 3.28`, frozen
`./eval_solution.sh solution <N>`, validity `p(mean<3.28) < 0.01`. Seed #83 re-measured:
`80.18s, val_loss 3.2774, p=0.00341` (N=3); `per_step ≈ 57.9ms × 1385`; `val_loss@1250 ≈ 3.331`
(convergence-bound at the tail).

```text
CAND mlp_post_only_hybrid — leaner ReLU² MLP storage, exact path on sensitive layers
  measured:    79.73s, val_loss=3.2765, p(mean<3.28)=0.01313 (N=3) -> INVALID
  decompose:   per-step win vs seed; convergence held; curve still crosses ~3.28 at the tail
  read:        mean 3.2765 CLEARLY inside 3.28, p just over 0.01 -> a POWER miss, not quality
  attribution: real per-step structural win (leaner MLP ≈ 1 of Recursive's inventions)
  decision:    N=10 certify NOW (mean this far inside almost always passes at N=10)
  re-measured: 79.77s ± 0.06, val_loss=3.2776 ± 0.0022, t=−3.389, p=0.004007 (N=10) -> VALID
  → next:      below ~79.5 needs the BIG lever — optimizer noise / schedule for step reduction,
               or stacking more inventions; single-mechanism-then-revert is spent
```

This is `N=10 certification` (§3, the statistical discipline) chosen because the miss was a
*power* miss — not "build another mechanism" (the trap) and not "tune the schedule" (premature).
A fuller failure-first version is in `Speedrun Hands-on Trace`; derive your own from your numbers.

---

## 11. Senior-level mistakes (sharper than "common pitfalls")

- Chasing per-step kernel/precision micro-opts (small, roofline-bounded lever) before the
  optimizer/schedule (the big, compounding lever).
- Reading a **power** miss (mean clearly inside, p just over at N=3) as a **quality** miss and
  re-engineering a recipe you have already won.
- Not banking a clearly-valid faster candidate immediately at N=10 — leaving the record un-cashed.
- Testing each mechanism in isolation and **reverting to the bare floor**, so improvements never
  stack toward a frontier built from stacked inventions.
- Naive step-count cuts on a convergence curve with no tail slack (fails the gate).
- FP8/low-precision without fused/cached scaling → net-slower or divergent, reported as "tried FP8".
- Declaring a win from a self-timed, N=1, or warm-state number instead of the frozen t-test.
- Restarting/interrupting **mid-certification** and losing a banked score; or comparing your number
  to a published anchor measured on different hardware without saying so.
- Grinding noise near the frontier (or re-certifying validity you already hold) instead of working
  the big lever or stopping honestly.

## Response shape
- Target metric, scorer command, hardware, the seed's re-measured baseline, and the named anchors
  in your harness units.
- The decomposition (per-step, convergence curve) and the bottleneck wall, with the evidence for it.
- The measured causal chain per candidate (frozen scorer numbers only), each INVALID labeled
  QUALITY or POWER.
- Absolute distance to the next anchor / frontier, and whether closing it needs the big lever or a
  real invention you don't yet have.
- The next mechanism chosen by the binding constraint, with the exact scorer command to run.
