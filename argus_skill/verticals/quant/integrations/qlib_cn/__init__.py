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
from .features import (
    build_feature_matrix,
    cross_sectional_normalize,
    forward_return_label,
    load_alpha360,
    time_split,
)
from .model import backtest_predictions, default_params, rolling_retrain_predict, train_predict
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
    # model pipeline (Alpha360 [+ fundamentals] -> GBDT -> OOS backtest)
    "load_alpha360",
    "build_feature_matrix",
    "cross_sectional_normalize",
    "forward_return_label",
    "time_split",
    "train_predict",
    "rolling_retrain_predict",
    "backtest_predictions",
    "default_params",
]
