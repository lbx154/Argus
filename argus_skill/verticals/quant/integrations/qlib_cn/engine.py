"""``QlibCnEngine`` — a REAL A-share backtest engine on the local qlib dump.

Implements the vertical's :class:`~...backtest.BacktestEngine` Protocol using
qlib's ``TopkDropoutStrategy`` + ``SimulatorExecutor`` over the local
``cn_data_tushare`` dump, with realistic A-share frictions (commission + stamp
duty via ``open_cost``/``close_cost``, ``min_cost``, 5 bps per-side slippage via
``impact_cost``, and ±limit non-tradability via ``limit_threshold`` +
``forbid_all_trade_at_limit``). Unlike the
:class:`~...reference_engine.ToyBacktestEngine` (synthetic panel) or the
``finance_argus`` wrapper (needs the finance_argus package, not installed here),
this engine needs only qlib + a local dump.

Signal source: the engine is handed a ``signal_provider`` mapping a
:class:`~...backtest.BacktestSpec` to a qlib ``(datetime, instrument)`` score
Series. :func:`make_toolkit_signal_provider` wires the market-agnostic
``factor_toolkit`` (or the alpha expression DSL) to it: load OHLCV from the dump
→ compute the factor → :func:`.data.factor_to_signal`.

Benchmark: this dump ships no index price series, so qlib's default
``SH000300`` benchmark is unavailable. With ``benchmark=None`` the engine uses a
present instrument only to satisfy qlib's report init and reports the
portfolio's OWN return (no index excess), disclosing this in ``warnings``.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ...backtest import BacktestResult, BacktestSpec, config_fingerprint
from . import data as _data

#: Resolve a spec to a qlib ``(datetime, instrument)`` score Series.
SignalProvider = Callable[[BacktestSpec], Any]


@dataclass
class QlibCnEngine:
    """A :class:`~...backtest.BacktestEngine` backed by qlib + the local dump.

    Attributes
    ----------
    signal_provider
        Maps a spec to the qlib score Series (see :func:`make_toolkit_signal_provider`).
    provider_uri
        Path to the qlib dump (default ``~/.qlib/qlib_data/cn_data_tushare``).
    topk, n_drop
        ``TopkDropoutStrategy`` holding size and per-rebalance rotation.
    benchmark
        Index instrument for excess-return reporting; ``None`` reports the
        portfolio's own return (this dump has no index series).
    open_cost, close_cost, min_cost, impact_cost, limit_threshold
        A-share frictions passed to qlib's exchange (defaults: 5bps buy /
        15bps sell incl. stamp duty, 5-yuan min, 5bps per-side slippage,
        ±9.5% limit).
    """

    signal_provider: SignalProvider
    provider_uri: str = _data.DEFAULT_PROVIDER_URI
    topk: int = 50
    n_drop: int = 5
    benchmark: str | None = None
    account: float = 1_000_000.0
    open_cost: float = 0.0005
    close_cost: float = 0.0015
    min_cost: float = 5.0
    impact_cost: float = 0.0005
    limit_threshold: float = 0.095
    name: str = "qlib-cn@v1"

    def _config_hash(self, spec: BacktestSpec) -> str:
        return config_fingerprint(
            engine_name=self.name,
            spec=spec,
            engine_config={
                "provider_uri": self.provider_uri,
                "topk": self.topk,
                "n_drop": self.n_drop,
                "benchmark": self.benchmark,
                "account": self.account,
                "open_cost": self.open_cost,
                "close_cost": self.close_cost,
                "min_cost": self.min_cost,
                "impact_cost": self.impact_cost,
                "limit_threshold": self.limit_threshold,
            },
        )

    def _cost_metadata(self) -> dict[str, Any]:
        """Audit payload matching the predeclared base model in plan/COST_MODEL.json."""
        return {
            "cost_model_id": "plan/COST_MODEL.json:base",
            "net_of_cost": True,
            "buy_cost_bps": self.open_cost * 10000.0,
            "sell_cost_bps": self.close_cost * 10000.0,
            "minimum_trade_cost_cny": self.min_cost,
            "slippage_bps_per_side": self.impact_cost * 10000.0,
            "limit_up_down_nontradable": self.limit_threshold is not None,
            "limit_threshold": self.limit_threshold,
            "suspended_or_missing_bar_nontradable": True,
            "next_bar_execution_required": True,
            "qlib_exchange_kwargs": {
                "freq": "day",
                "deal_price": "close",
                "open_cost": self.open_cost,
                "close_cost": self.close_cost,
                "min_cost": self.min_cost,
                "impact_cost": self.impact_cost,
                "limit_threshold": self.limit_threshold,
            },
            "tradability_mapping": {
                "only_tradable": True,
                "forbid_all_trade_at_limit": True,
                "suspended_or_missing_bar_nontradable": True,
            },
        }

    def _decision_metadata(self, spec: BacktestSpec, metrics: dict[str, float]) -> dict[str, Any]:
        thresholds = spec.params.get("decision_thresholds")
        if not thresholds:
            return {}
        candidate_id = str(spec.params.get("base_factor_id") or spec.run_id)
        status = str(spec.params.get("trial_type") or "")
        hits: list[str] = []
        if metrics.get("rank_ic_mean", 0.0) >= float(thresholds.get("rank_ic_mean_min", 0.015)):
            hits.append("rank_ic_mean")
        if metrics.get("rank_icir", 0.0) >= float(thresholds.get("rank_icir_min", 0.25)):
            hits.append("rank_icir")
        if metrics.get("rank_ic_positive_month_fraction", 0.0) >= float(
            thresholds.get("rank_ic_positive_month_fraction_min", 0.55)
        ):
            hits.append("positive_month_fraction")
        if metrics.get("rank_ic_t_stat", 0.0) >= float(
            thresholds.get("rank_ic_t_stat_min_before_multiple_testing_discount", 1.5)
        ):
            hits.append("rank_ic_t_stat")
        net_ok = metrics.get("long_short_net_return", 0.0) > float(
            thresholds.get("long_short_net_return_min", 0.0)
        )
        is_combo = status == "equal_weight_combination"
        if is_combo:
            best = spec.params.get("included_best_single_rank_icir")
            improves = best is not None and metrics.get("rank_icir", -999.0) > float(best)
            if len(hits) >= 2 and net_ok and improves:
                value = "combine"
                rationale = (
                    "Combination passes at least two ranking criteria, has positive net "
                    "validation spread, and improves RankICIR over the best included "
                    "canonical single."
                )
            else:
                value = "drop"
                rationale = (
                    "Combination did not satisfy survivor rule; "
                    f"hits={hits}, net_positive={net_ok}, "
                    f"improves_best_included_rank_icir={improves}."
                )
        elif len(hits) >= 2 and net_ok:
            value = "keep"
            rationale = (
                f"Passes survivor rule with ranking hits={hits} and positive validation "
                "net top-minus-bottom spread."
            )
        else:
            value = "drop"
            rationale = f"Does not meet survivor rule; ranking hits={hits}, net_positive={net_ok}."
        return {
            "candidate_id": candidate_id,
            "decision": {"value": value, "rationale": rationale, "criteria_hits": hits},
        }

    def _forward_alignment_metrics(self, signal: Any, spec: BacktestSpec) -> dict[str, float]:
        """Validation-only factor-at-t vs t->t+h forward-return diagnostics.

        This is opt-in because it queries qlib prices again. The run-stage
        screening specs enable it to bind RankIC/IC evidence to the same ledger
        row as the qlib backtest trial.
        """
        import numpy as np
        import pandas as pd
        from qlib.data import D

        holding = int(spec.params.get("holding_period_days", 5))
        label_end = pd.Timestamp(str(spec.params.get("forward_return_label_end")))
        if pd.isna(label_end):
            raise ValueError("forward_return_label_end is required for alignment metrics")

        sig = signal.rename("score").reset_index().pivot(
            index="datetime", columns="instrument", values="score"
        )
        sig.index = pd.to_datetime(sig.index)
        sig = sig.sort_index()
        if sig.empty:
            raise ValueError("empty signal for forward alignment")
        instruments = list(map(str, sig.columns))
        close = D.features(
            instruments,
            ["$close"],
            start_time=str(sig.index.min())[:10],
            end_time=str(label_end)[:10],
        )
        if close is None or len(close) == 0:
            raise ValueError("qlib returned no close prices for forward alignment")
        inst_level = "instrument" if "instrument" in close.index.names else close.index.names[0]
        close_wide = close["$close"].unstack(inst_level)
        close_wide.index = pd.to_datetime(close_wide.index)
        close_wide = close_wide.sort_index().reindex(columns=instruments)
        dates = list(close_wide.index)
        loc = {d: i for i, d in enumerate(dates)}

        rank_ics: list[float] = []
        ics: list[float] = []
        spreads: list[float] = []
        turnovers: list[float] = []
        coverage: list[float] = []
        months: list[str] = []
        prev_weights: pd.Series | None = None
        cost_rate = self.open_cost + self.close_cost + 2.0 * self.impact_cost

        for dt, score in sig.iterrows():
            if dt not in loc:
                continue
            label_idx = loc[dt] + holding
            if label_idx >= len(dates) or dates[label_idx] > label_end:
                continue
            px0 = close_wide.loc[dt]
            px1 = close_wide.iloc[label_idx]
            with np.errstate(divide="ignore", invalid="ignore"):
                fwd = px1 / px0 - 1.0
            paired = pd.DataFrame({"score": score, "fwd": fwd}).replace(
                [np.inf, -np.inf], np.nan
            ).dropna()
            if len(paired) < 20:
                continue
            rank_ic = paired["score"].rank().corr(paired["fwd"].rank())
            ic = paired["score"].corr(paired["fwd"])
            if pd.notna(rank_ic):
                rank_ics.append(float(rank_ic))
            if pd.notna(ic):
                ics.append(float(ic))
            coverage.append(float(len(paired) / max(1, score.notna().sum())))
            months.append(dt.strftime("%Y-%m"))

            n = max(1, int(len(paired) * 0.2))
            ordered = paired.sort_values("score")
            low = ordered.head(n)
            high = ordered.tail(n)
            gross = float(high["fwd"].mean() - low["fwd"].mean())
            weights = pd.Series(0.0, index=sig.columns, dtype=float)
            weights.loc[high.index] = 0.5 / len(high)
            weights.loc[low.index] = -0.5 / len(low)
            turnover = 1.0 if prev_weights is None else float((weights - prev_weights).abs().sum() / 2.0)
            prev_weights = weights
            turnovers.append(turnover)
            spreads.append(gross - turnover * cost_rate)

        def mean(xs: list[float]) -> float:
            return float(np.nanmean(xs)) if xs else 0.0

        def std(xs: list[float]) -> float:
            return float(np.nanstd(xs, ddof=1)) if len(xs) > 1 else 0.0

        ric_mean = mean(rank_ics)
        ric_std = std(rank_ics)
        ic_mean = mean(ics)
        ic_std = std(ics)
        spread_mean = mean(spreads)
        spread_std = std(spreads)
        if rank_ics:
            monthly = pd.DataFrame({"month": months[:len(rank_ics)], "rank_ic": rank_ics})
            pos_month = float((monthly.groupby("month")["rank_ic"].mean() > 0).mean())
        else:
            pos_month = 0.0
        equity = np.cumprod(1.0 + np.asarray(spreads, dtype=float)) if spreads else np.asarray([1.0])
        peak = np.maximum.accumulate(equity)
        drawdown = np.where(peak > 0, equity / peak - 1.0, 0.0)
        return {
            "holding_period_days": float(holding),
            "alignment_observation_days": float(len(rank_ics)),
            "rank_ic_mean": ric_mean,
            "rank_icir": float(ric_mean / ric_std) if ric_std > 0 else 0.0,
            "rank_ic_t_stat": float(ric_mean / (ric_std / np.sqrt(len(rank_ics)))) if ric_std > 0 else 0.0,
            "rank_ic_positive_month_fraction": pos_month,
            "ic_mean": ic_mean,
            "icir": float(ic_mean / ic_std) if ic_std > 0 else 0.0,
            "ic_t_stat": float(ic_mean / (ic_std / np.sqrt(len(ics)))) if ic_std > 0 else 0.0,
            "long_short_gross_return": mean([s + t * cost_rate for s, t in zip(spreads, turnovers)]),
            "long_short_net_return": spread_mean,
            "long_short_net_sharpe": float(spread_mean / spread_std * np.sqrt(252.0 / holding)) if spread_std > 0 else 0.0,
            "turnover_one_way": mean(turnovers),
            "max_drawdown": float(np.nanmin(drawdown)) if len(drawdown) else 0.0,
            "coverage_fraction": mean(coverage),
            "cost_bps_applied": cost_rate * 10000.0,
        }

    def run(self, spec: BacktestSpec) -> BacktestResult:
        """Run one real qlib backtest of the spec's factor signal."""
        signal = self.signal_provider(spec)
        if signal is None or len(signal) == 0:
            raise ValueError("signal_provider returned an empty signal")

        _data.qlib_init(self.provider_uri)
        from qlib.backtest import backtest
        from qlib.contrib.evaluate import risk_analysis
        from qlib.contrib.strategy import TopkDropoutStrategy

        dates = signal.index.get_level_values("datetime")
        warnings: list[str] = []
        bench = self.benchmark
        if bench is None:
            bench = str(signal.index.get_level_values("instrument")[0])
            warnings.append(
                "no index benchmark in dump; reporting portfolio-own return "
                f"(qlib benchmark set to {bench!r} only to satisfy report init)"
            )
        # Start a few bars in so the first rebalance has a prior price.
        uniq = sorted(dates.unique())
        start = str(uniq[min(3, len(uniq) - 1)])[:10]
        end = str(uniq[-1])[:10]

        # qlib settles the final rebalance on the NEXT bar, so if the signal
        # runs to the dump's last calendar day the executor indexes one past the
        # calendar and raises IndexError. Cap the end one trading day inside the
        # boundary (leave a next-day bar available).
        from qlib.data import D

        cal = D.calendar()
        if len(cal) >= 2:
            last_safe = str(cal[-2])[:10]
            if end >= last_safe:
                warnings.append(
                    f"backtest end {end} capped to {last_safe}: qlib needs a next-day "
                    f"settlement bar and the dump's last calendar day is {str(cal[-1])[:10]}"
                )
                end = last_safe
        if start >= end:
            raise ValueError(
                f"backtest window collapsed after boundary cap (start={start} >= end={end}); "
                "the signal is too close to the dump's calendar end"
            )

        strategy = TopkDropoutStrategy(
            signal=signal, topk=self.topk, n_drop=self.n_drop,
            only_tradable=True, forbid_all_trade_at_limit=True,
        )
        portfolio_metrics, _ind = backtest(
            start_time=start, end_time=end, strategy=strategy, benchmark=bench,
            account=self.account,
            executor={
                "class": "SimulatorExecutor", "module_path": "qlib.backtest.executor",
                "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True},
            },
            exchange_kwargs={
                "freq": "day", "limit_threshold": self.limit_threshold,
                "deal_price": "close", "open_cost": self.open_cost,
                "close_cost": self.close_cost, "min_cost": self.min_cost,
                "impact_cost": self.impact_cost,
            },
        )
        daily = portfolio_metrics.get("1day", portfolio_metrics)
        report = daily[0] if isinstance(daily, tuple) else daily
        ret = report["return"]  # portfolio-own return series (no index excess)
        risk = risk_analysis(ret).to_dict().get("risk", {})

        metrics = {
            "sharpe": float(risk.get("information_ratio") or 0.0),
            "annualized_return": float(risk.get("annualized_return") or 0.0),
            "max_drawdown": float(risk.get("max_drawdown") or 0.0),
            "cumulative_return": float((1.0 + ret).prod() - 1.0),
        }
        if spec.params.get("compute_forward_alignment"):
            metrics.update(self._forward_alignment_metrics(signal, spec))
        if spec.params.get("compute_forward_alignment"):
            warnings.append(
                "sharpe is qlib's information_ratio on portfolio-own returns; "
                "RankIC/IC fields are validation-only factor-at-t vs t->t+h "
                "forward-return diagnostics"
            )
        else:
            warnings.append(
                "sharpe is qlib's information_ratio on portfolio-own returns; "
                "no cross-sectional IC is computed by this engine"
            )
        metadata = self._cost_metadata()
        metadata.update(self._decision_metadata(spec, metrics))
        return BacktestResult(
            run_id=spec.run_id, status="ok", metrics=metrics,
            engine=self.name, config_hash=self._config_hash(spec),
            warnings=tuple(warnings), metadata=metadata,
        )


def make_toolkit_signal_provider(
    *,
    universe: str,
    start: str,
    end: str,
    feature: Any,
    provider_uri: str = _data.DEFAULT_PROVIDER_URI,
    instruments: Sequence[str] | None = None,
) -> SignalProvider:
    """Build a signal provider that computes ``feature`` over the qlib dump.

    ``feature`` is a :class:`~...factor_toolkit.builder.FeatureSpec` (e.g. from
    the alpha expression DSL or a built-in constructor). The provider loads the
    OHLCV panel once, computes the factor, and returns the qlib signal — so the
    same market-agnostic factor code that runs on the ToyEngine runs here on
    real A-share data. The provider ignores ``spec.factor_ids`` (the factor is
    fixed by ``feature``); use one provider per factor under test.
    """
    panel = _data.load_qlib_ohlcv(
        universe, start, end, provider_uri=provider_uri, instruments=instruments
    )
    ohlcv = {
        k: panel[k] for k in ("open", "high", "low", "close", "volume", "amount")
        if k in panel
    }
    factor_values = feature.compute(ohlcv)
    signal = _data.factor_to_signal(factor_values, panel["dates"], panel["codes"])

    def provider(_spec: BacktestSpec):
        return signal

    return provider
