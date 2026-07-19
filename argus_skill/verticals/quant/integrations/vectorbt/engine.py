"""vectorbt backtest-engine adapter (skeleton) for the quant vertical.

Wraps `vectorbt <https://vectorbt.dev/>`_ behind the vertical's
:class:`~...backtest.BacktestEngine` Protocol so a *single-asset, signal-based*
strategy can be driven by the ``ForcingExecutor`` and land search-ledger rows
like any other trial.

Paradigm note — this is a DIFFERENT shape of backtest from the cross-sectional
factor engine (:class:`~...reference_engine.ToyBacktestEngine`,
``finance_argus``). Those score a cross-section of instruments each bar and form
long/short portfolios; this one takes ONE instrument's price plus entry/exit
signals and simulates a P&L. It exists for the future single-asset markets the
vertical will grow into (crypto / futures), where signal strategies are the
native form.

Status: **adapter skeleton**. ``vectorbt`` is not a declared dependency and is
imported lazily inside :meth:`run`; until it is installed, running a trial
raises a clear ``ImportError`` (which the ``ForcingExecutor`` records as a
``status="error"`` ledger row — a failed trial is still an auditable trial).

Cost / slippage / bar-frequency are constructor parameters (market-specific);
nothing crypto/DEX is hardcoded.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ...backtest import BacktestResult, BacktestSpec, config_fingerprint

#: Resolve a spec to ``(close, entries, exits)`` for the single asset it names.
SignalProvider = Callable[
    [BacktestSpec], tuple[Sequence[float], Sequence[bool], Sequence[bool]]
]


@dataclass
class VectorbtEngine:
    """A :class:`~...backtest.BacktestEngine` backed by vectorbt (skeleton).

    Attributes
    ----------
    signal_provider
        Callable mapping a :class:`~...backtest.BacktestSpec` to
        ``(close, entries, exits)`` for the asset under test. Kept injectable so
        the engine is decoupled from where signals come from (a market
        integration supplies it).
    init_cash, fees, slippage, freq
        Backtest account and cost parameters passed straight to
        ``vectorbt.Portfolio.from_signals``. Set per market — e.g. A-share
        round-trip cost vs a crypto taker fee — no defaults are market-specific.
    name
        Provenance string recorded in the ledger.
    """

    signal_provider: SignalProvider | None = None
    init_cash: float = 1_000_000.0
    fees: float = 0.0
    slippage: float = 0.0
    freq: str = "1D"
    name: str = "vectorbt@skeleton"

    def _config_hash(self, spec: BacktestSpec) -> str:
        return config_fingerprint(
            engine_name=self.name,
            spec=spec,
            engine_config={
                "init_cash": self.init_cash,
                "fees": self.fees,
                "slippage": self.slippage,
                "freq": self.freq,
            },
        )

    def run(self, spec: BacktestSpec) -> BacktestResult:
        """Run one single-asset signal backtest through vectorbt.

        Raises ``ImportError`` if vectorbt is not installed and ``ValueError`` if
        no ``signal_provider`` is configured; both are captured as an ``error``
        ledger row by :func:`~...backtest.run_backtest`.
        """
        try:
            import vectorbt as vbt  # lazy: not a declared dependency
        except ImportError as exc:  # pragma: no cover - exercised only when absent
            raise ImportError(
                "VectorbtEngine requires vectorbt — install it with "
                "`pip install vectorbt` to run single-asset signal backtests"
            ) from exc

        if self.signal_provider is None:
            raise ValueError("VectorbtEngine.signal_provider is not configured")

        close, entries, exits = self.signal_provider(spec)
        portfolio = vbt.Portfolio.from_signals(
            close,
            entries,
            exits,
            init_cash=self.init_cash,
            fees=self.fees,
            slippage=self.slippage,
            freq=self.freq,
        )
        metrics = {
            "sharpe": float(portfolio.sharpe_ratio()),
            "total_return": float(portfolio.total_return()),
            "max_drawdown": float(portfolio.max_drawdown()),
        }
        return BacktestResult(
            run_id=spec.run_id,
            status="ok",
            metrics=metrics,
            engine=self.name,
            config_hash=self._config_hash(spec),
            warnings=("single-asset signal backtest — not a cross-sectional IC",),
        )
