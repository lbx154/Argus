"""backtrader backtest-engine adapter (skeleton) for the quant vertical.

Wraps `backtrader <https://www.backtrader.com/>`_ behind the vertical's
:class:`~...backtest.BacktestEngine` Protocol. Like :mod:`..vectorbt`, this is
the *single-asset, event-driven* backtest paradigm — bar-by-bar execution with
realistic order handling (bracket orders, stops) — as opposed to the
cross-sectional factor engine. It is aimed at the future single-asset markets
(crypto / futures) the vertical will grow into.

Status: **adapter skeleton**. ``backtrader`` is not a declared dependency and is
imported lazily inside :meth:`run`; until it is installed, running a trial
raises a clear ``ImportError`` (recorded by the ``ForcingExecutor`` as a
``status="error"`` ledger row). Commission / slippage are constructor
parameters — nothing market-specific is hardcoded.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...backtest import BacktestResult, BacktestSpec

#: Build a configured ``backtrader.Cerebro`` for the asset the spec names
#: (add the data feed + strategy). Injected so the engine stays decoupled from
#: the market data source.
CerebroBuilder = Callable[[BacktestSpec], Any]


@dataclass
class BacktraderEngine:
    """A :class:`~...backtest.BacktestEngine` backed by backtrader (skeleton).

    Attributes
    ----------
    cerebro_builder
        Callable that returns a configured ``Cerebro`` (data feed + strategy
        added) for a given spec. A market integration supplies it.
    init_cash, commission, slippage_perc
        Broker parameters applied to the built ``Cerebro`` before running. Set
        per market; no market-specific defaults.
    name
        Provenance string recorded in the ledger.
    """

    cerebro_builder: CerebroBuilder | None = None
    init_cash: float = 1_000_000.0
    commission: float = 0.0
    slippage_perc: float = 0.0
    name: str = "backtrader@skeleton"

    def _config_hash(self, spec: BacktestSpec) -> str:
        h = hashlib.sha256()
        for part in (self.name, str(self.commission), str(self.slippage_perc), *spec.factor_ids):
            h.update(str(part).encode())
        return h.hexdigest()[:16]

    def run(self, spec: BacktestSpec) -> BacktestResult:
        """Run one single-asset event-driven backtest through backtrader.

        Raises ``ImportError`` if backtrader is not installed and ``ValueError``
        if no ``cerebro_builder`` is configured; both are captured as an
        ``error`` ledger row by :func:`~...backtest.run_backtest`.
        """
        try:
            import backtrader as bt  # lazy: not a declared dependency
        except ImportError as exc:  # pragma: no cover - exercised only when absent
            raise ImportError(
                "BacktraderEngine requires backtrader — install it with "
                "`pip install backtrader` to run single-asset signal backtests"
            ) from exc

        if self.cerebro_builder is None:
            raise ValueError("BacktraderEngine.cerebro_builder is not configured")

        cerebro = self.cerebro_builder(spec)
        cerebro.broker.setcash(self.init_cash)
        cerebro.broker.setcommission(commission=self.commission)
        if self.slippage_perc:
            cerebro.broker.set_slippage_perc(self.slippage_perc)
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")

        start_value = cerebro.broker.getvalue()
        strat = cerebro.run()[0]
        end_value = cerebro.broker.getvalue()

        sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio")
        max_dd = strat.analyzers.drawdown.get_analysis().get("max", {}).get("drawdown")
        metrics = {
            "sharpe": float(sharpe) if sharpe is not None else 0.0,
            "total_return": float(end_value / start_value - 1.0),
            "max_drawdown": float(-(max_dd or 0.0) / 100.0),  # bt reports % positive
        }
        return BacktestResult(
            run_id=spec.run_id,
            status="ok",
            metrics=metrics,
            engine=self.name,
            config_hash=self._config_hash(spec),
            warnings=("single-asset event-driven backtest — not a cross-sectional IC",),
        )
