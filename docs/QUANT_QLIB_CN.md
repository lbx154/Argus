# Quant qlib-cn engine, OOS fix, runner & fundamental factors

A design + reference doc for the qlib-cn quant path added on top of the
`finance_argus` vertical. Covers what was built, the OOS crash it fixes, the
deterministic runner, the alpha DSL, the cost model, and the point-in-time
fundamental factors — plus the empirical findings and the known limits.

The same line of work also landed the broader **factor-generation & validation
toolkit** the vertical previously lacked: market-agnostic feature math
(`factor_toolkit/`), an evolutionary alpha search over the DSL, an overfitting-&-
validation analysis suite (`analysis/`), and alternate single-asset engines plus
a real A-share OHLCV loader. Those are documented in §7–§10.

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

## 7. Factor-generation toolkit (`factor_toolkit/`)

The feature layer the vertical previously lacked: pure numpy/pandas math that
turns raw price/OHLCV arrays into factor columns. Everything is
**market-agnostic** — features operate on `(T,)` / `(T,S)` arrays, any
annualisation is an explicit parameter, and no calendar/cost/tradability rule is
baked in (those stay in `integrations/<market>/`). Importing the package pulls
only numpy/pandas; sklearn is imported lazily inside `selection`, and the
package is **not** touched at vertical load.

- `price_features.py` — momentum, reversal, acceleration, log-return, high-low
  range, close-position, gap. Uses only information at/before each bar (warm-up
  positions are `NaN`).
- `volatility.py` — five annualised realized-vol estimators (close-to-close,
  Parkinson, Garman-Klass, EWMA); the range-based ones are lower-variance when
  OHLC is trustworthy.
- `statistical.py` — per-series mean-reversion / stationarity diagnostics:
  Hurst, AR(1) half-life, ADF, Lo-MacKinlay variance ratio, OU params. These are
  scalars that *describe a series* (decide whether a reversal thesis holds), not
  cross-sectional factor values.
- `regime.py` — ATR, ADX, Bollinger-band width + a 4-quadrant `classify_regime`,
  so the mining loop can gate/condition factors on the prevailing trend/vol
  state.
- `selection.py` — random-forest feature importance (in-sample MDI + the more
  trustworthy out-of-sample permutation) and correlation-based redundancy
  pruning, so the loop keeps a *diverse* factor set rather than many
  repackagings of one signal.
- `builder.py` — the bridge: `build_feature_panel` packages computed features
  into the vertical's `FactorSpec` / `ToyPanel` contract so they run straight
  through the existing `ToyBacktestEngine` and search ledger.

Much of the feature math is adapted from claude-trading-skills (MIT).

---

## 8. Evolutionary alpha search (`factor_toolkit/evolution.py`)

Where `builder` tests *known* factors and `expression` lets a human *compose*
one, `evolution.py` **discovers** new ones — a programmatic genetic search over
the alpha DSL, no LLM in the loop. Mutation operators are AST transforms
(perturb a window, swap an operator/field, wrap in a normalisation / non-linear
op, negate, simplify); `crossover` grafts a subtree from one expression into
another; `random_expression` samples a fresh valid expression (seeding / full
regeneration); and `evolve` runs the population loop against an **injected**
fitness (`make_panel_fitness` scores a candidate on a feature panel). Every
candidate is a real, validated DSL expression, so a discovered factor runs the
identical compute → signal → backtest pipe as a hand-written one.

---

## 9. Overfitting & validation analysis suite (`analysis/`)

New search-process diagnostics that complement the vertical's pre-existing
`multiple_testing` (Deflated Sharpe / BH-FDR) and `orthogonality`:

- `overfit.py` — **PBO** (probability of backtest overfitting) via combinatorial
  purged cross-validation (Bailey, Borwein, López de Prado & Zhu, 2017): how
  often the in-sample-best strategy underperforms out-of-sample — PBO > 0.5 means
  the selection process is more likely than not producing an overfit pick — plus
  minimum-backtest-length and Bonferroni / Holm family-wise-error masks (the FWER
  counterparts to the existing FDR mask).
- `factor_overfit.py` — the **IC view** of a single factor: `ic_stability`
  (per-period IC consistently signed and non-trivial), `subsample_stress` (sign
  holds across market regimes), `placebo_test` (real IC beats a
  cross-section-shuffle null, and a time-shifted factor decays), `ic_half_life`
  (fit `IC(h)=a·e^(−bh)`; the half-life should exceed the rebalance horizon), and
  a composite `factor_overfit_report`.
- `walk_forward.py` — time-ordered split generator with **purge** (drop the train
  bars whose forward label overlaps the test window) and **embargo** (skip bars
  after the boundary), the leakage guards standard k-fold lacks. Pure index
  arithmetic — carries no market assumption.
- `performance.py` — numpy-only portfolio metrics (CAGR, annualised vol,
  VaR/CVaR, drawdown & max-drawdown, Sharpe / Sortino / Calmar / Information
  ratio, trade stats); every annualised metric takes `periods_per_year`
  explicitly, so there is no hardcoded calendar.

---

## 10. Alternate engines & the adata OHLCV loader

- `integrations/backtrader/engine.py`, `integrations/vectorbt/engine.py` —
  **adapter skeletons** behind the `BacktestEngine` protocol for the
  *single-asset, signal / event-driven* backtest paradigm (bar-by-bar P&L on one
  instrument's price + entry/exit signals), as opposed to the cross-sectional
  factor engine. Aimed at the crypto / futures markets the vertical will grow
  into. Neither library is a declared dependency; both import lazily and raise a
  clear `ImportError` (recorded by the `ForcingExecutor` as a `status="error"`
  ledger row) until installed.
- `integrations/adata_cn/loader.py` — the real A-share **OHLCV** binding that
  feeds the toolkit (the price sibling of §6's PIT `fundamentals.py`):
  `load_ohlcv_panel` fetches adjusted daily OHLCV and pivots to the `(T,S)`
  cross-section, `forward_returns` builds the no-look-ahead scoring target, and
  `to_feature_panel` goes codes → panel → `ToyPanel` + registry in one call —
  runnable through the ForcingExecutor and search ledger.

---

## 11. Cross-family model pipeline (Alpha360 + fundamentals → GBDT)

The single-factor path (§1–§10) screens one alpha at a time; §11 learns a
non-linear cross-sectional **combination** of a whole factor library — where the
findings below argue OOS alpha must come from. Three additive modules (no change
to the committed engine/runner):

- `integrations/qlib_cn/features.py` — `load_alpha360` pulls qlib's canonical
  **360 technical features** straight off the dump; `build_feature_matrix`
  optionally left-joins the **PIT fundamental features** 1:1 on the
  `(datetime, instrument)` index, can apply a **per-day cross-sectional transform**
  (`normalize="rank"`/`"zscore"` — stops the model fighting scale/outliers), and
  attaches either the ~1-day Alpha360 label or a **`label_horizon`-day forward
  return** (longer horizons carry more signal-to-noise); `time_split` gives
  time-ordered train/valid/test masks (the test window is a genuine future).
- `integrations/adata_cn/fundamentals.py::fundamental_feature_frame` — a
  **~21-factor cross-family panel**: value (EP/BP/CFP + non-GAAP EP, per-share ÷
  price), quality/profitability (ROE, ROA, gross/net margin, cash-to-revenue,
  accruals), growth (revenue & profit YoY/QoQ), and balance-sheet health
  (leverage, liquidity, turnover). **`ytd_to_ttm`** de-seasonalises the per-share
  flows (`basic_eps`/`oper_cf_ps` are YTD-cumulative); ratios are fed as reported.
  All PIT-aligned by `notice_date` (§6).
- `integrations/adata_cn/cache.py` — a read-through pickle cache (`cached_fetcher`)
  so the ~500-call CSI500 fundamental fetch runs once, offline thereafter.
- `integrations/qlib_cn/model.py` — `train_predict` fits a lightgbm GBDT (qlib's
  `LGBModel` family) on train, early-stops on valid, predicts the quarantined
  test; **`rolling_retrain_predict`** refits every ~quarter on trailing (purged)
  history and stitches the OOS predictions, tracking concept drift the way a live
  deployment would; `backtest_predictions` turns the prediction into a qlib signal
  and runs it through the **same `QlibCnEngine`** (boundary cap, A-share frictions,
  RankIC/long-short forward-alignment, keep/drop decision, one ledger row) — so a
  model is judged exactly like a factor. Alpha360-only vs Alpha360+fundamental is
  the same code on two column subsets of one matrix, so the ONLY difference is the
  fundamental leg. Deterministic tests: `tests/test_quant_model_pipeline.py`.

---

## 12. Empirical findings so far

- **量价-only does not survive OOS.** Round-1 selected the Amihud illiquidity
  factor on validation (long-short Sharpe ≈ 2.68) but it **failed OOS**
  (RankIC ≈ 0, long-short Sharpe negative) — decision `drop`. The autonomous
  mission independently reproduced this and honestly downgraded to
  "validation-only".
- **Equal-weight combinations do not beat the best single factor OOS.** A search
  over all size-1..5 subsets found no combo beating single Amihud OOS; the
  factors are too homogeneous (all price/volume) to diversify.
- **Naive Alpha158 + LGBModel also fails out of the box** (OOS long-short Sharpe
  ≈ -0.17, model under-trained) — because Alpha158 is still technical-only.
- **The GBDT model is the first thing to survive OOS — but weakly.** On CSI500
  (train 2022-04…2023-12 / valid 2024-H1 / test-OOS 2024-07…2026-06), an Alpha360
  GBDT posts a *positive, consistent* OOS signal — RankIC ≈ +0.008, t ≈ 1.7,
  ~70% positive months, long-short net Sharpe ≈ +0.48 — where every single factor
  died. Learning a non-linear combination extracts what single factors could not.
  But **ICIR ≈ 0.08 is low: a weak edge, not a strong one.**
- **Adding fundamentals helped only marginally, and mixed.** Alpha360 + EP/BP/CFP
  + growth nudged RankIC (+0.0084→+0.0091), ICIR (0.080→0.092), t-stat
  (1.72→1.97) and positive-month fraction (0.70→0.74) up, but *lowered* the
  long-short net Sharpe (0.48→0.39). The model attributes only ~5% of its
  split-gain to the 5 fundamental features (though `fund_bp` and `fund_rev_yoy`
  reach the top-8). Verdict on this run: the cross-family leg is **not yet a
  decisive alpha source** — it improves the ranking metrics but not the tradable
  spread. Five quarterly features against 360 daily ones is likely drowned out.
- **The headline return is beta, not alpha.** The +39–42% annualised is
  **portfolio-own return over a strong 2024-25 A-share rally**; the dump has no
  index series, so no excess-return can be computed. The honest signal measure is
  the weak-positive RankIC, not the return.
- **The three levers turned ~0 into a real, tradable signal.** Same CSI500 OOS
  (2025-01…2026-06), gbdt: adding **21 PIT fundamentals** + **per-day cross-sectional
  rank normalisation** + a **20-day forward label** lifted OOS RankIC from ≈0 to
  **+0.030 (t ≈ 5, ~71% positive months)** — a genuinely strong market-neutral
  cross-sectional signal. But a *static* model still lost the tradable long-short
  spread (Sharpe −0.5, `drop`): its predictions drift out of calibration on the new
  regime. **Rolling retrain** (refit each quarter on trailing purged history) kept
  the same RankIC (+0.028, t≈3.2) but flipped the **long-short net Sharpe to +1.3,
  `keep`** — drift adaptation converting a predictive signal into a tradable one.
  This is the first positive, tradable OOS result — but it is **one window / one
  seed after a long search**; it still needs walk-forward confirmation + deflation
  by the full search breadth and a survivorship-clean universe before any
  "deployable alpha" claim.
- **Walk-forward + portfolio construction: the edge is real but is mostly the
  size/liquidity factor, not novel alpha.** An extended walk-forward (12 quarterly
  rolling retrains, 2023-07…2026-06) confirms the RankIC is broad (per-day mean
  +0.030, 59% positive) — but the *tradable* long-short is modest and lumpy
  (ann Sharpe ≈ **+0.5**, maxDD ≈ −13–23%, 2024 quarters negative; the +1.3 came
  from the strong 2025-26 regime). Improving construction on the same predictions —
  full-breadth signal weighting beat a naive quintile (Sharpe 0.38→0.45, maxDD
  −13%→−10%) — but **residualising the score against a size proxy collapsed it
  (0.45→0.09)**: most of the RankIC is the known **size/liquidity premium**, not a
  novel edge. Deflated ≈ 0.2–0.3; ≈ 0.2 at 2× cost. **Honest floor with this data:
  a real-but-weak, largely-size-driven signal — not clean deployable alpha.**

---

## 13. Autonomous model selection (`model_toolkit/`)

§11 fixes one model (a GBDT). But there is no universally best model — the right
choice is task-conditional and can drift — so `model_toolkit/` lets Argus *choose*
(and create) the model under discipline, mirroring the factor-mining loop:

- **Creating a model = emitting a config.** `registry.py::ModelSpec(family, config)`
  + `trainers.py` (gbdt / torch-MLP / ridge behind one `fit/predict` surface over
  numpy) make "pick a model" and "pick an architecture" the same act — L1 family,
  L2 config-level architecture (MLP depth/width/dropout), L3 (gated) authoring a
  novel `nn.Module`.
- **Task-conditional prior.** `task_profile.py` profiles the task (size, feature
  families, cross-section, an SNR proxy) and orders the space (tabular / low-SNR →
  trees & linear first; more data/features → MLPs earn prior mass). The prior only
  orders *what to try first* — the evidence decides the winner.
- **Disciplined selection.** `selection.py::select_model` runs **nested walk-forward**
  (purged/embargoed folds) with **successive halving** (all candidates on fold 0,
  keep the top half, survivors get more folds), scores by the **robust** median fold
  rank-IC, **ledgers every candidate×fold trial** (hash-chained, before the winner is
  known), and reports the **effective number of trials**
  (`analysis.multiple_testing.effective_num_trials` — eigenvalue participation ratio)
  so the OOS Sharpe is deflated by the search's *real* breadth, not a raw count.
- **The winner** is retrained on all development data and OOS-tested once via
  `model.backtest_predictions` (§11) — judged exactly like a factor.
- **The loop** (`skills/engineer/model-selection-loop.md`) is the LLM-in-the-loop
  contract: `select_models → evaluate_selection → decide(continue/stop/expand_space)`,
  standing rule "prefer the simpler model unless the complex one clears deflation".

Decision power to the agent, **veto power to the machine** (nested-WF + deflation +
OOS gate). Tests: `tests/test_quant_model_toolkit.py`.

---

## 14. Known limitations & next steps

- Reported returns are **portfolio-own**, not CSI500 excess — the dump has no
  index price series. No excess-return / IR-vs-index claims are permitted.
- Provider is **not** certified point-in-time / survivorship-free, and **CSI500
  membership in the dump is static (survivorship-biased)** — inflating in-sample
  and OOS alike.
- The model result is a **single train/valid/test split, one seed**; **walk-forward
  multi-split + Deflated Sharpe (§9) are not yet wired into the model path**, so the
  ICIR ≈ 0.08 edge is fragile and unconfirmed across folds.
- Turnover / DSR / PBO / CPCV are not yet wired for the qlib-cn path.
- **Next:** denser fundamentals (ROE, gross-profitability, accruals, more growth),
  cross-sectional rank/zscore of all features, a **longer label horizon**
  (fundamentals bite at weeks/months, not 1 day), and walk-forward + DSR hardening
- **Implemented since (§11):** denser fundamentals (~21 factors — ROE, margins,
  accruals, growth, leverage…), per-day cross-sectional rank/zscore normalisation,
  a longer (`label_horizon`-day) forward label, and rolling retrain for drift.
  These are the "did the data/method, not the model, move the OOS signal?" levers.
- **Next:** sequential DNNs (LSTM/Transformer via a qlib `DatasetH` + `GeneralPTNN`
  config) and qlib DDG-DA meta-learning as disciplined model-selection candidates;
  PBO/CSCV alongside the effective-trials deflation; and — if the edge stays
  marginal — moving the battleground (weekly/monthly rebalance where fundamentals
  dominate and costs bite less) rather than polishing the daily setup further.

---

## File map

```
argus_skill/verticals/quant/
  backtest.py                         # BacktestEngine/Spec/Result + cost metadata (§5)
  factor_toolkit/
    expression.py                     # alpha DSL + rolling_* aliases (§4)
    price_features.py                 # momentum/reversal/range/gap features (§7)
    volatility.py                     # realized/Parkinson/GK/EWMA vol (§7)
    statistical.py                    # Hurst/half-life/ADF/VR/OU diagnostics (§7)
    regime.py                         # ATR/ADX/BB-width + classify_regime (§7)
    selection.py                      # RF importance + redundancy pruning (§7)
    builder.py                        # features → FactorSpec/ToyPanel bridge (§7)
    evolution.py                      # genetic alpha search over the DSL (§8)
  model_toolkit/
    trainers.py                       # gbdt/MLP/ridge on numpy (config→model) (§13)
    registry.py                       # ModelSpec + default model space (§13)
    task_profile.py                   # task profile → model prior (§13)
    selection.py                      # nested-WF + halving selector, ledgered (§13)
  portfolio.py                        # score→dollar-neutral weights (breadth/vol/size-neutral) (§12)
  charting.py                         # candlestick (K-line) charts from OHLCV +/- signals
  analysis/
    overfit.py                        # PBO/CPCV, min-BTL, Bonferroni/Holm (§9)
    factor_overfit.py                 # IC stability/stress/placebo/half-life (§9)
    walk_forward.py                   # purged + embargoed WF splits (§9,§13)
    performance.py                    # portfolio metrics (§9)
    multiple_testing.py               # deflated Sharpe + effective_num_trials (§12,§13)
  integrations/qlib_cn/
    engine.py                         # QlibCnEngine + boundary cap (§2)
    runner.py                         # FactorTrial / run_trials (§3)
    data.py                           # dump loader, factor_to_signal
    features.py                       # Alpha360 + fundamentals matrix + splits (§11)
    model.py                          # GBDT train/predict → OOS backtest (§11)
  integrations/adata_cn/
    fundamentals.py                   # PIT factors + TTM + feature frame (§6,§11)
    cache.py                          # read-through fundamentals cache (§11)
    loader.py                         # real A-share OHLCV → toolkit panel (§10)
  integrations/backtrader/engine.py   # single-asset event-driven skeleton (§10)
  integrations/vectorbt/engine.py     # single-asset signal-based skeleton (§10)
tests/
  test_quant_qlib_cn_oos_boundary.py  # cap + runner regression (§2,§3)
  test_quant_adata_fundamentals.py    # PIT alignment (§6)
  test_quant_factor_toolkit.py        # feature builders + selection (§7)
  test_quant_expression.py            # alpha DSL / expressions (§4,§8)
  test_quant_factor_overfit.py        # IC overfit diagnostics (§9)
  test_quant_analysis_walk_forward.py # walk-forward splits (§9)
  test_quant_adata_loader.py          # OHLCV loader (§10)
  test_quant_model_pipeline.py        # Alpha360+fundamental GBDT pipeline (§11)
  test_quant_model_toolkit.py         # autonomous model selection (§13)
  test_quant_portfolio.py             # portfolio construction (§12)
  test_quant_charting.py              # K-line candlestick charts
skills/engineer/                      # (seeded when vertical=quant)
  quant-factor-loop.md · model-selection-loop.md
  kline-chart.md                      # render candlestick charts for reports
  quant-expert-persona.md             # senior quant/PM persona (skeptic's checklist)
```
