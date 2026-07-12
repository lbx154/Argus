"""qlib A-share backtest integration — real cross-sectional backtests.

See :mod:`.engine`. Provides :class:`~.engine.QlibCnEngine` (a
``BacktestEngine`` over the local qlib ``cn_data_tushare`` dump with realistic
A-share frictions) and helpers to load OHLCV / turn a factor into a qlib signal.
qlib is imported lazily inside the loader/engine, so importing this subpackage
never requires qlib to be installed.
"""
from __future__ import annotations

from .data import factor_to_signal, list_universe, load_qlib_ohlcv, qlib_init
from .engine import QlibCnEngine, SignalProvider, make_toolkit_signal_provider
from .runner import FactorTrial, run_trials, run_windowed_trial

__all__ = [
    "QlibCnEngine",
    "SignalProvider",
    "make_toolkit_signal_provider",
    "load_qlib_ohlcv",
    "factor_to_signal",
    "list_universe",
    "qlib_init",
    "FactorTrial",
    "run_windowed_trial",
    "run_trials",
]
