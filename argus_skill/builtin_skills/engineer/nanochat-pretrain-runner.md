---
name: NanoChat Pretrain Runner
description: Run the NANOCHAT pretraining task reproducibly on an A100 — train one self-contained solution.py under a 300s budget, evaluate mean val bpb across N seeds via run_with_shim.py on ds, beat the re-measured optimized_from_karpathy baseline, with manifest + background-launch + health-monitoring discipline and the verifier-rerun anti-cheat rule.
category: research-experiments
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-16T00:00:00+00:00
---

## Title
NanoChat Pretrain Runner

## Description
Execute the NANOCHAT TASK as an L1 engineer: minimize the **validation bits-per-byte (val bpb, lower = better)** of a small LLM pretrained from scratch under a **fixed 300s wall-clock budget on one A100**. This is a head-to-head against Recursive's automated-research system — whose `solution.py` gets the lower mean val bpb under the identical protocol. Unlike `agent-research-benchmark-runner`, the metric here is a **single pretraining run** scored as the **mean val bpb across N random seeds** — there is **NO method×benchmark/family matrix**. Your only deliverable is one self-contained `solution.py` training script.

## The task in one paragraph
BPB = average bits to encode each byte of held-out text; it is tokenizer-independent, so it is the clean quality signal for a from-scratch LM. The shared harness `lib.py` (tokenizer, dataloader, `evaluate_bpb`, `TIME_BUDGET=300`) is **frozen**. You write `solutions/<name>.py` which imports `lib`, trains for up to 300s on one A100, and prints `val_bpb:` on the held-out shard. You may change **only the training recipe inside your solution** (architecture, optimizer, LR schedule, data order, batch/seq sizing, init, etc.). You may **NOT** touch `lib.py`, the eval, the val set (`shard_06542`), or the budget. The reward that counts is what the **verifier** measures by re-running your solution, never your self-reported number.

## When to use
- The objective is the NANOCHAT pretraining task (minimize val bpb under the 300s/A100 protocol).
- You have a candidate `solution.py` (or a recipe idea) and need a clean, reproducible mean-val-bpb measurement.
- You need to (re)measure a baseline (`optimized_from_karpathy.py`) on our harness/hardware before claiming an improvement.

## When NOT to use
- The task is a method×family benchmark matrix → use `agent-research-benchmark-runner` instead.
- You only need to draft a plan / paper without launching a run.
- `ssh ds` / the `/scratch/recursive/nanochat_autoresearch` scaffold is unreachable and no run is possible.

## Harness layout (do not modify)
On the GPU node, scaffold root = `/scratch/recursive/nanochat_autoresearch`:
- `lib.py` — shared, FROZEN. Provides the tokenizer, the dataloader, `evaluate_bpb(...)`, and `TIME_BUDGET = 300` (seconds). Every solution does `import lib` (or `from lib import ...`).
- `solutions/<name>.py` — one self-contained training script per candidate. It must train within `TIME_BUDGET`, evaluate on the held-out **val shard `shard_06542`**, and print a line `val_bpb: <float>` to stdout.
- Data is wired at `/data`. Python interpreter is `/opt/conda/envs/ptca/bin/python`.
- Held-out val = `shard_06542` (never train on it, never touch the eval path).

## Hardware & the SDPA / A100 note (critical)
- GPU access is via SSH to host `ds` (alias for the 8×A100-40GB box `dashing-stork`): run everything as `ssh ds "<cmd>"`.
- **A100 cannot run flash-attn-3 (FA3 is Hopper/H100-B200 only).** Two ways to stay correct on A100:
  1. Run through the shim: `/scratch/run_with_shim.py <solution.py>` — it transparently swaps FA3 → torch SDPA at import time. Use this for any solution that pulls in FA3.
  2. Or write `solution.py` to use **torch SDPA** (`torch.nn.functional.scaled_dot_product_attention`) directly and skip the shim.
- Prefer the shim for baselines / unknown code; prefer native SDPA for your own clean solutions. Either way the measured run must be the one that ran on the A100 through a working attention path.

## How to run a solution (single run, N seeds)
1. **Smoke first** — one seed, confirm it trains, respects the budget, and prints `val_bpb:`:
   ```bash
   ssh ds 'cd /scratch/recursive/nanochat_autoresearch && \
     SEED=0 timeout 360 /opt/conda/envs/ptca/bin/python /scratch/run_with_shim.py solutions/solution.py' \
     2>&1 | tee experiments/<run_id>/seed0.smoke.log
   ```
   - `SEED` is the env var the harness reads to seed the run. `timeout 360` is a hard safety wall above the internal 300s `TIME_BUDGET` (allow ~60s for import/eval); the **300s budget is enforced inside the run**, not by your timeout.
   - If it OOMs, errors, or prints no `val_bpb:`, fix the recipe before going wider. Never report a partial run.
2. **N seeds for the real measurement.** Iterate at **N=3–5**; for a final/headline report use **more (Recursive used 10)**. Vary only `SEED`; everything else identical:
   ```bash
   for SEED in 0 1 2 3 4; do
     ssh ds "cd /scratch/recursive/nanochat_autoresearch && \
       SEED=$SEED timeout 360 /opt/conda/envs/ptca/bin/python /scratch/run_with_shim.py solutions/solution.py" \
       2>&1 | tee experiments/<run_id>/seed${SEED}.log
   done
   ```
   Each run is ~300s; N=5 ≈ 25–30 min wall-clock — launch it in the background (see discipline below), do not sit and watch.
3. **Parse `val_bpb:` and compute the mean.** The score is the mean across seeds of the per-seed `val_bpb:`:
   ```bash
   grep -h '^val_bpb:' experiments/<run_id>/seed*.log | awk '{print $2}' \
     | python3 -c 'import sys; v=[float(x) for x in sys.stdin]; \
       import statistics as s; \
       print(f"n={len(v)} mean={s.mean(v):.4f} std={(s.pstdev(v) if len(v)>1 else 0):.4f} vals={v}")'
   ```
   Record per-seed values AND the mean/std. The **mean val bpb is the number you compare**; std tells you whether N is large enough (if std is large relative to the gap to baseline, raise N).

## Baseline to beat
- The target is Recursive's released best solution, **`optimized_from_karpathy.py`**, **re-measured on OUR harness/hardware** — not its published number.
- Its published score is **0.9109 on a B200**; you must **re-measure it on the A100** under the identical protocol (same N seeds, same 300s, same val shard, through the shim) and beat **that re-measured mean val bpb**. Do not compare your A100 number against the B200 paper number.
- Always (re)run the baseline in the same mission/session as your candidate so both numbers come from the same harness state. Report: baseline mean±std (N), your mean±std (N), and the delta (negative = win).

## Search strategy (basin-hopping + co-tuning, NOT greedy hill-climbing)
The recipe space is multi-modal: the early wins are easy (e.g. 1.145 → 1.109) but then a greedy loop — every experiment restarted from the SAME verified incumbent and forced to beat it with ONE change immediately — STALLS in a local optimum, making <0.001 nibbles forever. Run the search as **basin-hopping with co-tuning** instead:
- **GLOBAL BEST vs ACTIVE LINE.** Snapshot the lowest-ever **verifier-measured** mean val bpb as the **GLOBAL BEST** — that is the deliverable floor and you NEVER lose it (keep its `solution.py` + per-seed CSVs archived). But do **NOT** restart every experiment from the global-best recipe. Maintain a separate **ACTIVE LINE** — the recipe you are currently developing — which may sit *temporarily above* the global best while it matures.
- **Maturation window — don't snipe a bold idea after one round.** A structural / optimizer / architecture change usually scores **WORSE on round 1** because its supporting hyperparameters (LR, init, warmup, schedule, batch/seq sizing) do not fit it yet. Give every new direction a **maturation window of ~2–4 rounds of co-tuning** before judging it. Declaring a direction dead after a single losing measurement is the central mistake.
- **Combine coordinated changes into ONE candidate.** When a structural change and the hyperparameters that support it express **one idea** (e.g. deeper net + lower LR + longer warmup; a new optimizer + its ε/β/weight-decay), ship them together in a single `solution.py`. Do **not** force one-knob-at-a-time on a method-level move — that is what guarantees round-1 looks like a regression.
- **Basin-hop after ~3 nibbles.** If the last ~3 rounds each improved the global best by **<0.001** (or failed), you are in a local optimum: STOP perturbing the current recipe and open a **NEW active line from a structurally DIFFERENT point** — a different depth/width trade, a different attention scheme, a different optimizer regime, a different token/step-budget split, a curriculum, a different normalization/residual scheme. Develop that new line for several rounds **even if it is temporarily worse** than the global best. You are exploring the landscape, not climbing one hill.
- **Revert the active line's last step, not all the way back.** Keeping the floor safe means *snapshotting* the global best, not *re-anchoring* to it: when a co-tuning step regresses, revert the active line's **last** change and continue from there — only fall back to the global-best recipe when you are deliberately starting a fresh basin-hop from the known-good floor.
- **Bias to bold.** At least **half** the rounds must be structural / method-level explorations (new architecture, optimizer, or training paradigm), not regularizer/init/LR nibbles. The nibbles are nearly exhausted; the remaining gains live in a different region of the design space, which only bold moves + patient co-tuning will reach.

## Manifest + background-launch + health-monitoring discipline
Reuse the reproducibility discipline from `agent-research-benchmark-runner`, **specialized to a single-run pretraining metric (one solution, N seeds, no method×family matrix).**
- **Run id + manifest before launching:** `experiments/nanochat-<short>-<YYYYMMDDTHHMMSSZ>/`. Write `manifest.json` first with: objective (`minimize mean val bpb`), `solution_path`, exact command, host (`ds`/dashing-stork A100), `python`, attention path (`shim` or `native_sdpa`), `TIME_BUDGET=300`, `seeds`, `val_shard=shard_06542`, baseline being compared, and a source snapshot of `solution.py`. Also create `status.json`, `progress.jsonl`, `stdout.log`, `stderr.log`, and a `STOP` cancellation file convention.
- **Background launch, don't block:** anything >60s (i.e. every real run, ~300s each) goes to the background. Capture `pid`, stream each seed to `experiments/<run_id>/seed${SEED}.log`, append a `progress.jsonl` line before/after each seed (flush/fsync), and atomically update `status.json`. After confirming the PID is alive and seed 0 produced a `val_bpb:`, **switch to other work** (prep the next recipe variant, draft the comparison table against the expected schema) instead of polling.
- **Health checks specific to this metric — verify each seed:**
  - **Per-seed val bpb present and sane:** every seed log ends with exactly one `val_bpb:` line; values are finite and in a plausible band (roughly ~0.85–1.3 for this scale — a NaN/inf, a `val_bpb:` far below baseline, or 0.0 is a bug/leak, not a win). Large seed-to-seed variance → raise N or fix nondeterminism.
  - **No OOM / no crash:** scan stderr for `CUDA out of memory`, FA3/Hopper import errors (means the shim/SDPA path was bypassed), and tracebacks. An OOM or crashed seed is an invalid run, not a slow one — fix sizing (per-device batch, seq len, grad accum) and rerun that seed.
  - **GPU saturation:** during a run, `ssh ds nvidia-smi` should show high util% and meaningful VRAM on the A100 — a from-scratch LM under a 300s budget should be filling the card, not crawling. Low util%/VRAM means the recipe is under-using the budget (batch/seq too small); record peak VRAM + util% + step-time and raise the sizing. Going small is allowed only for a documented smoke run.
  - **Budget honored:** the internal training loop must stop at `TIME_BUDGET=300`; a run that only finishes because of your outer `timeout` (i.e. ignored the budget) is invalid.
- **Collect in the same mission:** when the background run finishes (PID exits / `status.json` terminal), parse all seeds, compute mean±std, write `RUN_REPORT.md` (commands, per-seed `val_bpb:`, mean±std, baseline delta, attention path, peak VRAM/util%, caveats), and update `status.json` to `completed`. Do not leave a finished run uncollected.
- **STOP discipline:** check for `STOP` between seeds; on cancel write `run_cancelled`, set status `cancelled`, exit 130.

## Anti-cheat rule (read this twice)
- **The reward is the val bpb the VERIFIER computes by re-running your `solution.py` under the identical protocol** (N seeds, 300s, held-out `shard_06542`, A100 via shim/SDPA). Your self-reported number is never the reward.
- Therefore the only thing that moves the score is a genuinely better **training recipe inside `solution.py`**. You may NOT touch `lib.py`, the evaluator, the val set, or the budget — the verifier uses the frozen versions regardless, so any local edit to them is a no-op at best and a disqualifying tamper at worst.
- Do not special-case `shard_06542`, peek at / memorize val data, hardcode an output, shortcut the eval, or game the timer. Any gap between your reported mean and the verifier's re-measured mean is treated as the verifier's number — and as a cheating signal. Make `solution.py` self-contained and deterministic-per-`SEED` so the verifier reproduces exactly what you reported.

## Response shape
- Report the run id, the exact `ssh ds … run_with_shim.py solutions/solution.py` command, attention path used, per-seed `val_bpb:` values, and the **mean±std (N)**.
- Report the re-measured `optimized_from_karpathy` baseline mean±std (N) and the signed delta; do not claim a win unless your mean < re-measured baseline mean with the seed logs present and quoted.
- Never claim success from a self-reported number alone — point at the seed logs / `RUN_REPORT.md` that the verifier could reproduce.
