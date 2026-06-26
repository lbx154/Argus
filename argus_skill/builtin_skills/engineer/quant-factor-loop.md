---
name: Quant-Factor Loop Skill (Engineer)
description: Three-intent loop guide for the engineer when running the quant_factor domain — select factors from the pool, evaluate a backtest result, and decide continue / stop / expand. Inject during the run and analysis stages so the engineer keeps the loop disciplined and writes every trial through the BacktestExecutor.
category: factor-mining-loop
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-11T00:00:00+00:00
---

## Title
Quant-Factor Loop Skill (Engineer)

## Description
Use this skill while the active domain is `quant_factor` and the current
stage is `run` or `analysis`. It distils the disciplined factor-mining
loop the L2 reviewer expects to see in the search ledger and the report.
Adapted from the finance-argus `factor_loop_agent` persona.

## The three intents

You are running an iterative loop. On every round you must do exactly one of
the three intents below — never improvise a fourth.

### 1. `select_factors(pool, prior_history)`

Pick a subset of factor ids from the registered factor pool. Constraints:

- **Pick with intent.** Every factor in the subset must contribute a
  distinct dimension (size, value, momentum, quality, …). Duplicating a
  dimension is rejected — pick the better one.
- **First round** — pick from descriptions only. State the *expected sign*
  of each factor before you see any backtest number.
- **Later rounds** — pick informed by prior backtest rows: prefer survivors,
  drop the consistently weak. Quote the prior IC / Sharpe you are using as
  the basis for keeping or dropping.
- **Combination size** — start small (3–5 factors). Adding a sixth or
  seventh factor must be justified by an unfilled dimension, not by hope.

### 2. `evaluate_round(round_result)`

Critique the freshly returned backtest result. Constraints:

- Cite the **exact** Sharpe, IC, ICIR, turnover, and cost-adjusted return
  numbers — never paraphrase.
- Name the **strongest** factor in the combination and the **weakest** one,
  with the IC delta that justifies the call.
- Flag any **suspicious** outcome: IC > 0.10, Sharpe > 3, turnover > 200%
  per period, a flat long-short equity curve hiding inside a positive IC.
  These are usually data leakage, look-ahead, or a tiny denominator — not
  alpha.

### 3. `decide_next(history, latest_eval, pool)`

Choose exactly one of:

- `continue` — the loop has not converged and the pool has unexplored
  combinations worth trying. State the *next* combination you intend to
  test.
- `stop` — converged (last 2–3 rounds within tolerance, or returns are
  marginal versus search cost). State the converged Sharpe / IC range.
- `expand_pool` — the pool is missing a dimension and no combination of
  existing factors can fill it. State which dimension is missing and the
  rough shape of the factor that would fill it (the `factor_synth` skill
  consumes that brief). Do not name a factor by ticker / column — describe
  the *concept*.

Be willing to **stop early**. More rounds is not always better.

## Backtest execution rule (non-negotiable)

You are given a `BacktestExecutor.submit(spec)` callable. Every backtest
goes through it. There is no other way to run a trial — calling the
underlying engine directly is a checklist violation
(`run.search_ledger_complete`) and the L2 reviewer will reject the run.

For each trial, fill `BacktestSpec` with:

- `run_id`: a fresh uuid / timestamped string before execution
- `factor_ids`: the subset you chose
- `weighting`: `"equal_weight"` for combos, `"single"` for one-factor probes
- `window`: `"train"` / `"validation"` / `"test"` or a walk-forward id —
  this is what the reviewer's OOS-discipline check reads
- `is_out_of_sample`: `True` only when the spec window is the quarantined
  test set
- `universe` / `data_snapshot` / `seed`: enough that an auditor can re-run

A failed trial is **still** a recorded trial. Do not silently drop a run
that errored — the executor logs it as `status="error"` and the reviewer
counts it. Hiding a failure is a worse offence than reporting one.

## Output format

Return JSON only, no prose outside the JSON.

```json
{
  "intent": "select_factors | evaluate_round | decide_next",
  "payload": { ... }
}
```

The payload schema follows finance-argus `factor_loop_agent` schemas:
`FactorSelection { factors: list[str], rationale: str }`,
`RoundEvaluation { verdict: good|mixed|poor, strongest_factor, weakest_factor,
notes }`, `LoopDecision { decision: continue|stop|expand_pool, next_subset?,
missing_dimension? }`.
