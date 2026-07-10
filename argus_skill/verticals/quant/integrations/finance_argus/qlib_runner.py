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
    train_start: str = "2020-01-01",  # noqa: ARG001 — interface parity; this unfitted runner ignores it (see docstring)
    train_end: str = "2022-12-31",  # noqa: ARG001 — interface parity; this unfitted runner ignores it (see docstring)
    test_start: str = "2023-01-01",
    test_end: str = "2024-06-30",
    topk: int = 50,
    n_drop: int = 5,
    benchmark: str | None = None,
    pool: Any = None,
) -> dict[str, Any]:
    """Run a real qlib backtest of a declared-weight factor subset.

    ``benchmark`` is a qlib instrument code present in the dump (e.g.
    ``"SZ000905"``); ``None`` (default) reports portfolio-return metrics with no
    index excess. Returns ``{sharpe, mean_ic, max_drawdown, cumulative_return,
    top_n_picks, _engine, _factors_used, _iteration, _elapsed_seconds}``.

    ``universe`` genuinely restricts the scored/traded pool: it is resolved via
    qlib's own ``D.instruments``/``D.list_instruments`` membership tables, so
    e.g. ``"csi300"`` vs ``"csi500"`` produce different instrument sets and
    different backtests. (Previously this parameter was accepted but never
    consulted anywhere in this function — every universe silently ran the
    identical backtest. Fixed below.)

    ``train_start``/``train_end`` are accepted — forwarded unchanged from
    :meth:`~.engine.FinanceArgusEngine._invoke`'s uniform "qlib kind" calling
    convention (see that class's ``backtest_fn``/``backtest_fn_kind``
    docstring: "the *kind* selects the calling convention explicitly, no
    signature introspection") — but **this particular runner does not fit
    anything on them**. It scores the factor subset once at ``test_start``
    with the *declared* (not train-window-fit) combination weights that
    :meth:`~.engine.FinanceArgusEngine.run` already discloses via its
    "combination weights recorded are declared, not realised IC weights"
    warning. They exist in this signature so a real train-fitting
    ``backtest_fn`` (e.g. finance-argus' own ``qlib_backtest_for_loop``,
    which per :class:`~.windows.WindowSchedule`'s docstring *does* fit on
    this window) can be swapped into ``FinanceArgusEngine.backtest_fn``
    without changing ``_invoke``'s calling convention. If this runner's
    scoring is later extended to fit weights on a training window, this is
    where ``train_start``/``train_end`` should be threaded through.
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

    # qlib must be initialised before any `qlib.data.D` query (used below to
    # resolve `universe`) — moved ahead of the scoring step for that reason.
    init_qlib_bridge()

    from qlib.backtest import backtest as qlib_backtest
    from qlib.contrib.evaluate import risk_analysis
    from qlib.contrib.strategy import TopkDropoutStrategy
    from qlib.data import D

    # Score the cross-section once at test_start (static signal for the window),
    # mirroring finance-argus' own one-shot eval.
    _, screen = market.build_market_screen(test_start, pure_quant=True, progress_callback=None)
    ranked = fm.score_cross_section(screen)

    sig_series = pd.Series(
        ranked["quant_score"].astype(float).values,
        index=[ts_to_qlib_code(c) for c in ranked["ts_code"].astype(str)],
    ).rename("score")

    # Restrict the scored/traded pool to the requested qlib universe. `D.
    # instruments`/`D.list_instruments` is qlib's own real universe-membership
    # lookup (the same index-membership tables `benchmark` is drawn from), so
    # this makes different `universe` values (e.g. "csi300" vs "csi500")
    # produce genuinely different backtests instead of all silently sharing
    # whatever `build_market_screen` happened to return.
    if universe:
        members = set(
            D.list_instruments(
                D.instruments(market=universe),
                start_time=test_start,
                end_time=test_end,
                as_list=True,
            )
        )
        sig_series = sig_series[sig_series.index.isin(members)]
        if sig_series.empty:
            raise ValueError(
                f"universe={universe!r} matched none of the scored instruments "
                f"for [{test_start}, {test_end}]; check the universe name against "
                "the qlib dump's instruments, or the factor screen"
            )

    test_dates = pd.date_range(test_start, test_end, freq="B")
    sig_multi = pd.concat({d: sig_series for d in test_dates}, names=["datetime", "instrument"])

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
        "_universe": universe,
        "_factors_used": list(factor_names),
        "_iteration": iteration,
        "_elapsed_seconds": round(time.time() - started, 1),
    }
