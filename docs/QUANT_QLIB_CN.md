# Quant qlib-cn engine, OOS fix, runner & fundamental factors

A design + reference doc for the qlib-cn quant path added on top of the
`finance_argus` vertical. Covers what was built, the OOS crash it fixes, the
deterministic runner, the alpha DSL, the cost model, and the point-in-time
fundamental factors — plus the empirical findings and the known limits.

> File map is at the bottom. All code lives under
> `argus_skill/verticals/quant/`.

---

## 1. Two quant backtest paths

The quant vertical now has **two** real backtest engines behind the same
`BacktestEngine` protocol (`backtest.py`):

| Path | Module | Data | Status |
|---|---|---|---|
| `finance_argus` | `integrations/finance_argus/` | needs the private `finance_argus` pkg | upstream default |
| **`qlib-cn@v1`** | `integrations/qlib_cn/` | local `~/.qlib/qlib_data/cn_data_tushare` dump (qlib only) | **added here** |

The qlib-cn path needs only `qlib` + a local dump, so it runs in this
environment where `finance_argus` does not. It is the path used for every run in
this line of work.

Signal flow: a factor **expression** → `factor_toolkit` computes a `(T,S)` array
→ `data.factor_to_signal` → qlib `TopkDropoutStrategy` + `SimulatorExecutor` with
A-share frictions (commission + stamp duty, min cost, ±limit non-tradability).

---

## 2. The OOS boundary-cap fix (the crash this work started from)

**Symptom.** Every quarantined-test OOS trial died with
`IndexError: index 1068 is out of bounds for axis 0 with size 1068`.

**Root cause.** The OOS evaluation window ends on the dump's *last* calendar day
(e.g. `2026-06-04`, calendar length 1068, valid indices 0..1067). qlib settles
the final rebalance on the **next** bar, so it indexes one past the calendar.
The earlier boundary cap lived only in `QlibCnEngine.run`, but at the time the
OOS trials ran that cap was not yet present, and — more importantly — the OOS
window end was *pinned to the last calendar day*, so extending the data by a day
just moved the boundary and re-created the crash.

**Fix** (`integrations/qlib_cn/engine.py`, `QlibCnEngine.run`): cap the backtest
end to the **second-to-last** calendar day (`cal[-2]`) so qlib always has a
next-day settlement bar; disclose the cap in `warnings`. Verified: the 4
previously-failing OOS trials now complete with no `IndexError`, and the
autonomous mission reached the OOS stage cleanly.

Regression tests (`tests/test_quant_qlib_cn_oos_boundary.py`, mocked qlib — run
without a dump): a window ending on the last calendar day is capped and warned;
a window ending inside is left untouched.

---

## 3. The deterministic runner (no more hand-rolled OOS)

Previously the engineer hand-wrote the run/OOS orchestration each mission
(building the window, computing the signal, calling qlib) — which is exactly
where the boundary bug slipped in. `integrations/qlib_cn/runner.py` makes the
capped engine path the **only** path:

- `FactorTrial(candidate_id, factors, weights?, standardize="zscore")` — a single
  factor or a weighted combination. A combo is compiled to a single alpha-DSL
  formula `Σ wᵢ · zscore(exprᵢ)` (cross-sectional standardisation before
  weighting), so it runs the identical compute→signal→backtest pipe as a single
  factor — no divergent combo code.
- `run_windowed_trial(...)` / `run_trials(...)` drive each trial through
  `run_backtest` (so the boundary cap always applies and every trial — success
  or failure — is ledgered), with **warm-up history sliced off the signal**:
  data is loaded from `history_start` for the rolling look-back, then the signal
  is trimmed to `[test_start, test_end]` so the backtest spans only the
  evaluation window.

---

## 4. Alpha-expression DSL: `rolling_*` aliases

`factor_toolkit/expression.py` is an AST-safe WorldQuant-style DSL. Frozen
factor expressions used pandas-style `rolling_mean(...)` / `rolling_std(...)`,
which the DSL did not recognise (it had `ts_mean` / `ts_std`). Added aliases
`rolling_mean→ts_mean`, `rolling_std→ts_std`, `rolling_sum/min/max` — identical
semantics — so those expressions parse verbatim instead of forcing a hand-rolled
feature path.

---

## 5. Cost-model provenance in ledger rows

`backtest.py`'s `BacktestResult` gained a `metadata` map, and `_result_payload`
records the A-share cost model into every ledger row (`cost_model_id`,
`net_of_cost`, `buy/sell_cost_bps`, `minimum_trade_cost_cny`,
`slippage_bps_per_side`, `limit_up_down_nontradable`, …). This is additive over
the upstream adapter — an auditor can see the frictions a row was scored under.

---

## 6. Point-in-time fundamental factors (the missing leg)

The `cn_data_tushare` dump ships **only OHLCV** (`open/high/low/close/volume/
amount/factor`) — every round-1 factor was price/volume, and qlib's `Alpha158`
is likewise pure technical. The fundamental leg comes from **adata**:

`integrations/adata_cn/fundamentals.py` calls
`adata.stock.finance.get_core_index`, which returns quarterly core financials
per stock **with a `notice_date` (公告日)**. `pit_align_field` aligns each report
point-in-time to the trading calendar — a report's values become usable only
on/after its `notice_date`, forward-filled until the next report — killing the
look-ahead / restatement leakage that naive `report_date` alignment causes.
`fundamental_factor` composes signed value factors: **EP** (`eps/price`), **BP**
(`net_asset_ps/price`), **CFP** (`oper_cf_ps/price`). Proven on CSI500: 100% PIT
coverage, sensible magnitudes (BP median ≈ 0.36, EP median ≈ 2.2%).
Deterministic no-network tests (`tests/test_quant_adata_fundamentals.py`) guard
the alignment.

---

## 7. Empirical findings so far

- **量价-only does not survive OOS.** Round-1 selected the Amihud illiquidity
  factor on validation (long-short Sharpe ≈ 2.68) but it **failed OOS**
  (RankIC ≈ 0, long-short Sharpe negative) — decision `drop`. The autonomous
  mission independently reproduced this and honestly downgraded to
  "validation-only".
- **Equal-weight combinations do not beat the best single factor OOS.** A search
  over all size-1..5 subsets found no combo beating single Amihud OOS; the
  factors are too homogeneous (all price/volume) to diversify.
- **Naive Alpha158 + LGBModel also fails out of the box** (OOS long-short Sharpe
  ≈ -0.17, model under-trained) — because Alpha158 is still technical-only. This
  is why the fundamental leg (§6) matters: it adds a cross-family, low-correlation
  factor source a model can actually exploit.

---

## 8. Known limitations & next steps

- Reported returns are **portfolio-own**, not CSI500 excess — the dump has no
  index price series. No excess-return / IR-vs-index claims are permitted.
- Provider is **not** certified point-in-time / survivorship-free for the price
  universe.
- Turnover / DSR / PBO / CPCV are not yet wired for the qlib-cn path.
- **Next:** scale fundamentals to the full CSI500, add ROE / gross-profitability
  / growth, then combine **fundamental + Alpha158** into an `LGBModel` (Rung-2)
  with train/valid/test time-window splits and evaluate OOS — the real test of
  whether cross-family factors finally produce OOS alpha.

---

## File map

```
argus_skill/verticals/quant/
  backtest.py                         # BacktestEngine/Spec/Result + cost metadata (§5)
  factor_toolkit/expression.py        # alpha DSL + rolling_* aliases (§4)
  integrations/qlib_cn/
    engine.py                         # QlibCnEngine + boundary cap (§2)
    runner.py                         # FactorTrial / run_trials (§3)
    data.py                           # dump loader, factor_to_signal
  integrations/adata_cn/
    fundamentals.py                   # PIT fundamental factors (§6)
tests/
  test_quant_qlib_cn_oos_boundary.py  # cap + runner regression (§2,§3)
  test_quant_adata_fundamentals.py    # PIT alignment (§6)
```
