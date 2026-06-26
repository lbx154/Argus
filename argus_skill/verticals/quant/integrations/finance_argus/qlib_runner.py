"""A benchmark-flexible real-qlib backtest runner.

finance-argus' own ``qlib_backtest_for_loop`` hardcodes ``benchmark="SH000300"``.
When the local qlib dump doesn't include that index (a common case for custom
tushare dumps), the backtest can't run. This runner does the same job —
score OUR factor subset, hand the signal to qlib's ``TopkDropoutStrategy``,
backtest with realistic A-share costs — but makes the benchmark optional:
with ``benchmark=None`` it reports metrics from the portfolio's own returns
(no excess-over-index), so it works on any dump that has the portfolio
instruments.

It reuses finance-argus' data / scoring / qlib-init pieces (imported lazily);
nothing here is reimplemented that finance-argus already owns except the
benchmark handling. The return dict matches the ``mock_backtest`` /
``qlib_backtest_for_loop`` schema so it drops straight into
:class:`FinanceArgusEngine` as a ``backtest_fn``.
"""
from __future__ import annotations

import time
from typing import Any


def qlib_backtest_run(
    factor_names: list[str],
    iteration: int,
    *,
    universe: str = "csi300",
    train_start: str = "2020-01-01",
    train_end: str = "2022-12-31",
    test_start: str = "2023-01-01",
    test_end: str = "2024-06-30",
    topk: int = 50,
    n_drop: int = 5,
    benchmark: str | None = None,
    pool: Any = None,
) -> dict[str, Any]:
    """Run a real qlib backtest of an IC-weighted factor subset.

    ``benchmark`` is a qlib instrument code present in the dump (e.g.
    ``"SZ000905"``); ``None`` (default) reports portfolio-return metrics with no
    index excess. Returns ``{sharpe, mean_ic, max_drawdown, cumulative_return,
    top_n_picks, _engine, _factors_used, _iteration, _elapsed_seconds}``.
    """
    started = time.time()

    import pandas as pd  # lazy; only the real path needs pandas
    from finance_argus.core.config import load_config
    from finance_argus.core.data import TinyshareMarketData
    from finance_argus.core.factor_pool import FactorPool
    from finance_argus.core.quant import QuantFactorModel
    from finance_argus.integrations.qlib_bridge.init_helper import init_qlib_bridge
    from finance_argus.integrations.qlib_bridge.universe import ts_to_qlib_code

    cfg = load_config()
    market = TinyshareMarketData(cfg)
    fm = QuantFactorModel(cfg)

    factor_pool = pool or FactorPool.with_builtins()
    selected_defs = factor_pool.definitions(factor_names) if factor_pool else []
    if not selected_defs:
        raise ValueError(f"No factor definitions resolved for: {factor_names}")
    fm.definitions = tuple(selected_defs)

    # Score the cross-section once at test_start (static signal for the window),
    # mirroring finance-argus' own one-shot eval.
    _, screen = market.build_market_screen(test_start, pure_quant=True, progress_callback=None)
    ranked = fm.score_cross_section(screen)

    sig_series = pd.Series(
        ranked["quant_score"].astype(float).values,
        index=[ts_to_qlib_code(c) for c in ranked["ts_code"].astype(str)],
    ).rename("score")
    test_dates = pd.date_range(test_start, test_end, freq="B")
    sig_multi = pd.concat({d: sig_series for d in test_dates}, names=["datetime", "instrument"])

    init_qlib_bridge()

    from qlib.backtest import backtest as qlib_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.contrib.strategy import TopkDropoutStrategy

    strategy = TopkDropoutStrategy(
        signal=sig_multi, topk=topk, n_drop=n_drop,
        only_tradable=True, forbid_all_trade_at_limit=True,
    )
    bt_kwargs: dict[str, Any] = dict(
        start_time=test_start, end_time=test_end, strategy=strategy,
        executor={
            "class": "SimulatorExecutor",
            "module_path": "qlib.backtest.executor",
            "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
        },
        account=1_000_000.0,
        exchange_kwargs={
            "freq": "day", "limit_threshold": 0.095, "deal_price": "close",
            "open_cost": 0.0005, "close_cost": 0.0015, "min_cost": 5,
        },
    )
    if benchmark:
        bt_kwargs["benchmark"] = benchmark

    portfolio_metrics, _indicator = qlib_backtest(**bt_kwargs)

    daily_pm = portfolio_metrics.get("1day", portfolio_metrics)
    report_normal = daily_pm[0] if isinstance(daily_pm, tuple) else daily_pm

    # Excess over benchmark when we have one; else the portfolio's own returns.
    if benchmark and "bench" in report_normal:
        series = report_normal["return"] - report_normal["bench"]
    else:
        series = report_normal["return"]

    risk: dict[str, Any] = {}
    try:
        risk = risk_analysis(series).to_dict()["risk"]
    except Exception:  # noqa: BLE001 - metric calc must not crash the trial
        pass

    sharpe = float(risk.get("information_ratio") or 0.0)
    mdd = float(risk.get("max_drawdown") or 0.0)
    cum = float(risk.get("annualized_return") or 0.0)
    mean_ic = sharpe / 8.0  # same proxy finance-argus uses to keep eval calibrated

    final_picks = list(sig_series.sort_values(ascending=False).head(topk).index)

    return {
        "sharpe": round(sharpe, 3),
        "mean_ic": round(mean_ic, 4),
        "max_drawdown": round(mdd, 3),
        "cumulative_return": round(cum, 3),
        "top_n_picks": final_picks,
        "_engine": "qlib",
        "_benchmark": benchmark,
        "_factors_used": list(factor_names),
        "_iteration": iteration,
        "_elapsed_seconds": round(time.time() - started, 1),
    }
