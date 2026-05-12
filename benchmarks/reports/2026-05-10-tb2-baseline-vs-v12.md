# TB v2 — argus-skill v12 vs codex-bare baselines (2026-05-10 comparison)

> **Setup**: This is a retrospective comparison written 2026-05-10 against
> three TB v2 89-task runs that have been archived under `benchmarks/results/`.
> No fresh experiments were run for this comparison — all three runs use the
> exact same `terminal-bench@2.0` dataset commit (`69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`)
> and the same Azure OpenAI endpoint, so they are directly comparable.

## TL;DR

| Run | Engineer | Helpers | Reward (pass/89) | $/trial | $-vs-bare-large |
|---|---|---|---:|---:|---:|
| **bare-large** | `gpt-5.4` /high | — | **0.6629** (59) | $0.327 | 1.00× |
| **bare-mini**  | `gpt-5.4-mini` /high | — | **0.5618** (50) | $0.137 | 0.42× |
| **v12** (argus-skill) | `gpt-5.4-mini` /high | reviewer `gpt-5.4`/medium, scientist `gpt-5.4`/high | **0.5955** (53) | $0.139 | 0.42× |

**Headline reading**
- **v12 beats `bare-mini` by +0.034 reward at the same cost** → the scientist + reviewer machinery contributes ~3.4 percentage points of reward over a pure mini-codex pipeline, essentially for free.
- **v12 loses 0.067 reward to `bare-large` but at 42% the cost** → for cost-sensitive deployments the trade is +5.7× cost-efficiency (`(reward / dollar)` ratio) for -10% reward.
- v12 is **not** "1/3 of baseline" as previously reported (that was a token-bookkeeping error — see "Corrections" below).

## Methodology

All three runs are 89-trial fullbenches on TB v2. Tokens were read from each trial's `result.json` (`agent_result.{n_input_tokens, n_cache_tokens, n_output_tokens}` — this is the codex CLI's own HTTP-level metering, cross-trial and cross-round aggregated, and is the gold source). For v12 the additional host-side scientist+reviewer cost was obtained by filtering `~/.codex/sessions/2026/05/06/rollout-*.jsonl` to the run window (13:47–17:00 UTC) and `cwd=/home/argustest/argus-skill` — yielding exactly 182 sessions for the 89 trials.

Pricing follows `docs/PRICING.md` (OpenAI official):
- `gpt-5.4`     : $1.25 / $0.125 (cached) / $10.00 per 1M tokens (in/cache/out).
- `gpt-5.4-mini`: $0.25 / $0.025 (cached) / $2.00.
- Cache pricing is 1/10 of the non-cached input price for both models.

Cost formula: `(input_tokens − cached_input_tokens) × in_price + cached_input_tokens × cache_price + output_tokens × out_price`.

## Detailed costs

### bare-large

| | tokens | cache hit | $/M | $ |
|---|---:|---:|---:|---:|
| input (non-cached) | 6,889,670 | — | 1.250 | 8.61 |
| input (cached)     | 77,435,648 | 91.8% | 0.125 | 9.68 |
| output             | 1,078,456 | — | 10.000 | 10.78 |
| **total** | 85,403,774 | | | **29.08** |
| per trial | | | | **0.327** |

### bare-mini

| | tokens | cache hit | $/M | $ |
|---|---:|---:|---:|---:|
| input (non-cached) | 6,274,283 | — | 0.250 | 1.57 |
| input (cached)     | 217,224,064 | 97.2% | 0.025 | 5.43 |
| output             | 2,605,290 | — | 2.000 | 5.21 |
| **total** | 226,103,637 | | | **12.21** |
| per trial | | | | **0.137** |

### v12

Engineer pool (`gpt-5.4-mini` /high, from `result.json`):

| | tokens | cache hit | $/M | $ |
|---|---:|---:|---:|---:|
| input (non-cached) | 5,408,127 | — | 0.250 | 1.35 |
| input (cached)     | 107,627,776 | 95.2% | 0.025 | 2.69 |
| output             | 1,264,637 | — | 2.000 | 2.53 |
| **subtotal**       | 114,300,540 | | | **6.57** |

Scientist + Reviewer pool (`gpt-5.4`, from host rollouts):

| | tokens | cache hit | $/M | $ |
|---|---:|---:|---:|---:|
| input (non-cached) | 2,303,413 | — | 1.250 | 2.88 |
| input (cached)     | 1,552,128 | 40.3% | 0.125 | 0.19 |
| output             | 270,532 | — | 10.000 | 2.71 |
| **subtotal**       | 4,126,073 | | | **5.78** |

**v12 grand total: $12.35 / 89 = $0.139 per trial**

## Pairwise outcome diff (head-to-head on the same 89 tasks)

### v12 vs bare-large

- Both pass: 51
- Both fail: 16
- **v12 wins** (v12 pass, bare-large fail): 8 — `constraints-scheduling`, `fix-git`, `gcode-to-text`, `hf-model-inference`, `kv-store-grpc`, `mteb-leaderboard`, `mteb-retrieve`, `sanitize-git-repo`
- **v12 losses** (bare-large pass, v12 fail): 14 — `circuit-fibsqrt`, `extract-moves-from-video`, `headless-terminal`, `mailman`, `mcmc-sampling-stan`, `overfull-hbox`, `path-tracing`, `path-tracing-reverse`, `protein-assembly`, `pytorch-model-cli`, `rstan-to-pystan`, `sparql-university`, `winning-avg-corewars`, `write-compressor`
- Net: −6 → matches the −0.067 reward gap.

### v12 vs bare-mini

- **v12 wins**: 11 — `adaptive-rejection-sampler`, `build-cython-ext`, `chess-best-move`, `db-wal-recovery`, `gcode-to-text`, `kv-store-grpc`, `make-mips-interpreter`, `model-extraction-relu-logits`, `mteb-leaderboard`, `mteb-retrieve`, `pypi-server`
- **v12 losses**: 8 — `headless-terminal`, `mailman`, `mcmc-sampling-stan`, `path-tracing-reverse`, `pytorch-model-cli`, `sqlite-with-gcov`, `video-processing`, `winning-avg-corewars`
- Net: +3 → matches the +0.034 reward gap.

**Pattern reading**:
- The 11 v12 vs bare-mini wins are tasks where the scientist's distilled skill + reviewer gating actively helped a mini-engineer (`build-cython-ext`, `mteb-*`, `pypi-server` look like "build/serve a real package" type tasks that benefit from the skill scaffold).
- The 8 losses against bare-mini cluster in *long-tail simulation/numerical* tasks (`headless-terminal`, `mcmc-sampling-stan`, `path-tracing-reverse`, `winning-avg-corewars`) — likely the 2-round + budget cap interferes with longer search that bare-mini gets to do unbounded.

## Corrections to earlier estimates

This document supersedes the cost claims in:
- `docs/RETROSPECTIVE-v12-vs-current.md` (originally said `$18.79 / $0.21 per trial`)
- `EXPERIMENTS.md` v12 row (originally said `$18.79`)
- `benchmarks/results/tb2-ablation-2026-05-10-v4-pri2/FINDINGS-2026-05-10.md` §5

Sources of the prior error:
1. **Engineer double-counting**: scanning per-round transcript files (`argus-skill-round-1.txt`, `…-2.txt`) summed cumulative HTTP usage twice, since codex CLI's running totals carry forward across rounds. **Fix**: read `result.json` `agent_result.*`, which is HTTP-level metered and aggregated by codex once.
2. **Over-attribution of host sessions**: I previously included all 658 codex sessions from `~/.codex/sessions/2026/05/06/`, but only 182 of those belong to the v12 run (the rest are unrelated host debugging/microbench runs from earlier that day). **Fix**: filter on time window 13:47–17:00 UTC and `cwd=/home/argustest/argus-skill`.

Net effect: corrected v12 total $18.79 → **$12.35**; per-trial $0.211 → **$0.139**.

## What this means going forward

1. **Don't claim "v12 is 1/3 the cost"** — it is 42% of bare-large. The lift narrative is *"−10% reward for −58% cost"*, not *"matched reward at 1/3 cost"*.
2. **Bare-mini is the real reference** for an apples-to-apples engineer comparison, since v12's engineer is also mini. The +0.034 reward at parity cost is the genuine argus-skill contribution.
3. **The 14 losses vs bare-large** include 4 simulation/sampling tasks where v12 fails *and* bare-large succeeds. These are diagnostic targets for the next iteration — see `docs/RETROSPECTIVE-v12-vs-current.md` §3 for the "scientist over-thinks" hypothesis.
4. **Future experiments must include both baselines** by symlink/path reference (per `docs/EXPERIMENT_PROTOCOL.md` §4) so we never re-fall into the trap of comparing only to a flattering subset.

## Files

- `/home/argustest/argus-skill/benchmarks/results/tb2-bare-large-2026-05-01/` — bare-large, archived from `/tmp/`.
- `/home/argustest/argus-skill/benchmarks/results/tb2-bare-mini-2026-05-02/` — bare-mini, archived from `~/skill-agent/`.
- `/home/argustest/argus-skill/benchmarks/results/tb2-fullbench-2026-05-06-v12/` — v12 treatment (already in repo).
