"""Gradient-boosted model over the Alpha360[+fundamental] matrix, backtested OOS.

The model path the vertical's §12 roadmap calls for: train a GBDT (lightgbm — the
same family as qlib's ``LGBModel``) to predict forward returns from the whole
factor library, then judge it out-of-sample the SAME way a single factor is judged
— its cross-sectional predictions become a qlib signal that runs through
:class:`~.engine.QlibCnEngine` (boundary cap, A-share frictions, RankIC/long-short
forward-alignment, keep/drop decision) and lands one hash-chained ledger row.

Two calls, one shared matrix: :func:`train_predict` (fit on train, early-stop on
valid, predict on the quarantined test window) and :func:`backtest_predictions`
(score → engine → ledger). The Alpha360-only vs Alpha360+fundamental experiment is
just the same functions on two column subsets of one matrix, so the ONLY
difference is the fundamental leg.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ...backtest import BacktestResult, BacktestSpec
from ...executor import ForcingExecutor
from ...search_ledger import LedgerRow, SearchLedger
from . import data as _data
from .engine import QlibCnEngine


def default_params(seed: int = 0) -> dict[str, Any]:
    """Reasonable GBDT hyper-params for cross-sectional return regression."""
    return {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "bagging_freq": 5,
        "min_data_in_leaf": 100,
        "lambda_l1": 1.0,
        "lambda_l2": 1.0,
        "seed": seed,
        "verbosity": -1,
    }


def train_predict(
    X: Any,
    y: Any,
    splits: dict[str, Any],
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = 1000,
    early_stopping: int = 50,
    seed: int = 0,
    min_row_coverage: float = 0.5,
):
    """Fit on ``train``, early-stop on ``valid``, predict on ``test``.

    Returns ``(booster, pred_series, info)`` where ``pred_series`` is a
    ``(datetime, instrument)`` score over the test window (rows with <
    ``min_row_coverage`` finite features dropped so thin stock-days do not enter
    the signal), and ``info`` carries best-iteration / sizes / top feature gains.
    """
    import lightgbm as lgb
    import pandas as pd

    def _xy(mask):
        Xm, ym = X[mask], y[mask]
        keep = ym.notna().to_numpy()
        return Xm.to_numpy()[keep], ym.to_numpy()[keep]

    Xtr, ytr = _xy(splits["train"])
    Xva, yva = _xy(splits["valid"])
    params = params or default_params(seed)
    dtrain = lgb.Dataset(Xtr, label=ytr)
    dvalid = lgb.Dataset(Xva, label=yva, reference=dtrain)
    booster = lgb.train(
        params, dtrain, num_boost_round=num_boost_round,
        valid_sets=[dtrain, dvalid], valid_names=["train", "valid"],
        callbacks=[lgb.early_stopping(early_stopping, verbose=False), lgb.log_evaluation(0)],
    )

    Xte = X[splits["test"]]
    cov = Xte.notna().mean(axis=1)
    Xte = Xte[cov > min_row_coverage]
    pred = booster.predict(Xte.to_numpy(), num_iteration=booster.best_iteration)
    pred_series = pd.Series(pred, index=Xte.index, name="score")

    gains = booster.feature_importance(importance_type="gain")
    names = list(X.columns)
    top = sorted(zip(names, gains), key=lambda kv: -kv[1])[:15]
    info = {
        "best_iteration": int(booster.best_iteration or 0),
        "n_train": int(len(ytr)), "n_valid": int(len(yva)), "n_test": int(len(pred_series)),
        "n_features": len(names),
        "top_features": [(n, float(g)) for n, g in top],
        "fund_gain_fraction": float(
            sum(g for n, g in zip(names, gains) if n.startswith("fund_")) / max(1.0, float(sum(gains)))
        ),
    }
    return booster, pred_series, info


def rolling_retrain_predict(
    X: Any,
    y: Any,
    *,
    family: str,
    config: dict[str, Any],
    oos_start: str,
    oos_end: str,
    step_days: int = 63,
    purge_days: int = 0,
    lookback_days: int | None = None,
    min_train_days: int = 250,
    min_row_coverage: float = 0.5,
    seed: int = 0,
):
    """Retrain periodically over the OOS window and stitch the predictions.

    Every ``step_days`` (≈ a quarter at 63) the model is refit on the history
    STRICTLY BEFORE that chunk (expanding, or a ``lookback_days`` rolling window),
    then predicts the chunk — so it tracks concept drift the way a live deployment
    would, and uses only past data at each point. The last ``purge_days`` of each
    training window are dropped so a ``horizon``-day forward label cannot overlap
    the chunk (set ``purge_days = label_horizon``). Returns ``(pred_series, windows)``
    where ``windows`` documents each retrain for the audit trail.
    """
    import pandas as pd

    from ...model_toolkit.trainers import build_trainer

    dtidx = X.index.get_level_values("datetime")
    all_days = dtidx.unique().sort_values()
    oos_days = list(all_days[(all_days >= pd.Timestamp(oos_start)) & (all_days <= pd.Timestamp(oos_end))])
    Xnp, ynp = X.to_numpy(), y.to_numpy().astype("float64")

    preds: list[Any] = []
    windows: list[dict[str, Any]] = []
    for i in range(0, len(oos_days), step_days):
        chunk = oos_days[i : i + step_days]
        cutoff = chunk[0]
        train_days = all_days[all_days < cutoff]
        if purge_days > 0:
            train_days = train_days[: len(train_days) - purge_days]
        if lookback_days:
            train_days = train_days[-lookback_days:]
        if len(train_days) < min_train_days:
            continue
        k = max(1, int(len(train_days) * 0.15))
        fit_days, valid_days = train_days[:-k], train_days[-k:]
        fit_ok = np.asarray(dtidx.isin(fit_days)) & np.isfinite(ynp)
        va_ok = np.asarray(dtidx.isin(valid_days)) & np.isfinite(ynp)
        trainer = build_trainer(family, {**config, "seed": seed})
        trainer.fit(Xnp[fit_ok], ynp[fit_ok], Xnp[va_ok], ynp[va_ok])
        Xc = X[np.asarray(dtidx.isin(chunk))]
        Xc = Xc[Xc.notna().mean(axis=1) > min_row_coverage]
        preds.append(pd.Series(trainer.predict(Xc.to_numpy()), index=Xc.index))
        windows.append({"retrain_at": str(cutoff)[:10], "train_days": int(len(train_days)),
                        "predict_from": str(chunk[0])[:10], "predict_to": str(chunk[-1])[:10]})

    pred_series = pd.concat(preds).sort_index() if preds else pd.Series(dtype="float64")
    pred_series.name = "score"
    return pred_series, windows


def backtest_predictions(
    pred_series: Any,
    *,
    universe: str,
    ledger: SearchLedger,
    run_id: str,
    window_label: str,
    forward_return_label_end: str,
    model_id: str = "alpha360",
    holding_period_days: int = 5,
    is_out_of_sample: bool = True,
    provider_uri: str = _data.DEFAULT_PROVIDER_URI,
    topk: int = 50,
    n_drop: int = 5,
    open_cost: float = 0.0005,
    close_cost: float = 0.0015,
    min_cost: float = 5.0,
    impact_cost: float = 0.0005,
    limit_threshold: float = 0.095,
    decision_thresholds: dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
) -> tuple[BacktestResult, LedgerRow]:
    """Backtest a model's ``(datetime, instrument)`` prediction as a qlib signal.

    Reuses :class:`QlibCnEngine` verbatim (boundary cap + frictions + forward
    alignment) via a provider that returns the frozen prediction, and records one
    ledger row through :class:`ForcingExecutor`. Mirrors the params contract of
    ``runner.run_windowed_trial`` so the row is auditable the same way.
    """
    signal = pred_series.dropna()
    if len(signal) == 0:
        raise ValueError("empty prediction signal")

    engine = QlibCnEngine(
        signal_provider=lambda _spec: signal,
        provider_uri=provider_uri, topk=topk, n_drop=n_drop,
        open_cost=open_cost, close_cost=close_cost, min_cost=min_cost,
        impact_cost=impact_cost, limit_threshold=limit_threshold,
    )
    params: dict[str, Any] = {
        "model_id": model_id,
        "signal_names": int(signal.index.get_level_values("instrument").nunique()),
        "compute_forward_alignment": True,
        "forward_return_label_end": forward_return_label_end,
        "holding_period_days": holding_period_days,
        "trial_type": "model_prediction",
        "base_factor_id": model_id,
        "decision_thresholds": decision_thresholds or {},
        "runtime_universe": universe,
        "ledger_universe": universe,
        "topk": topk, "n_drop": n_drop,
        "cost_model_id": "plan/COST_MODEL.json:base",
        "net_of_cost": True,
        "buy_cost_bps": open_cost * 10000.0,
        "sell_cost_bps": close_cost * 10000.0,
        "minimum_trade_cost_cny": min_cost,
        "slippage_bps_per_side": impact_cost * 10000.0,
        "limit_up_down_nontradable": limit_threshold is not None,
    }
    if extra_params:
        params.update(extra_params)
    spec = BacktestSpec(
        run_id=run_id,
        factor_ids=[model_id],
        weighting="model",
        params=params,
        window=window_label,
        is_out_of_sample=is_out_of_sample,
        universe=universe,
        seed=0,
    )
    return ForcingExecutor(engine=engine, ledger=ledger).submit(spec)
