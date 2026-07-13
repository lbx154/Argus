"""Market-agnostic factor-construction toolkit for the quant vertical.

The *factor-generation* layer the vertical previously lacked: pure numpy/pandas
functions that turn raw price / OHLCV / return arrays into factor features,
plus a :func:`build_feature_panel` bridge that packages computed features into
the vertical's :class:`~..factors.FactorSpec` contract so they run straight
through the existing :class:`~..reference_engine.ToyBacktestEngine` and search
ledger.

Everything here is market-agnostic — features operate on ``(T,)`` / ``(T, S)``
arrays and any annualisation is an explicit parameter. The raw data binding
(A-share / futures / crypto) stays in ``integrations/<market>/``; only the
feature math lives here.

Submodules:

* :mod:`.price_features` — momentum, reversal, acceleration, range, gap.
* :mod:`.statistical` — Hurst, half-life, ADF, variance ratio, OU (diagnostics).
* :mod:`.volatility` — realized / Parkinson / Garman-Klass / EWMA vol.
* :mod:`.regime` — ATR, ADX, BB width, 4-quadrant :func:`classify_regime`.
* :mod:`.selection` — random-forest importance + redundancy pruning (sklearn).
* :mod:`.builder` — :func:`build_feature_panel` / :class:`FeatureSpec` bridge.

Heavy deps (sklearn) are imported lazily inside :mod:`.selection`; importing
this package pulls only numpy/pandas. It is NOT imported at vertical load
(``load_vertical("quant")`` still touches only ``stages``).
"""
from __future__ import annotations

from . import evolution, price_features, regime, selection, statistical, volatility
from .builder import (
    FeatureSpec,
    OHLCVPanel,
    build_feature_panel,
    default_feature_catalog,
)
from .evolution import (
    EvolutionResult,
    crossover,
    evolve,
    make_panel_fitness,
    mutate,
    random_expression,
)
from .expression import (
    ExpressionError,
    available_operators,
    evaluate,
    expression_feature,
)

__all__ = [
    "available_operators",
    "build_feature_panel",
    "crossover",
    "default_feature_catalog",
    "evaluate",
    "evolution",
    "evolve",
    "EvolutionResult",
    "ExpressionError",
    "expression_feature",
    "FeatureSpec",
    "make_panel_fitness",
    "mutate",
    "OHLCVPanel",
    "price_features",
    "random_expression",
    "regime",
    "selection",
    "statistical",
    "volatility",
]
