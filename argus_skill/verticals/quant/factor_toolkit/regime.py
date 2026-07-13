"""Market-regime indicators — market-agnostic per-asset classification.

Single-asset trend / volatility indicators (ATR, ADX, Bollinger-band width) and
a 4-quadrant :func:`classify_regime` that turns summary statistics into regime
labels. These characterise ONE instrument's state over time (ADX/ATR are not
cross-sectional), so a factor-mining loop can gate or condition factors on the
prevailing regime (e.g. only trade a reversal factor when the series is
mean-reverting and ranging).

Rolling / EWM computations use pandas internally; 1-D array in, 1-D array out.

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
regime-detection/scripts/detect_regime.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_ArrayLike = np.ndarray | list


def _s(x: _ArrayLike) -> pd.Series:
    return pd.Series(np.asarray(x, dtype=float).ravel())


def atr(high: _ArrayLike, low: _ArrayLike, close: _ArrayLike, *, period: int = 14) -> np.ndarray:
    """Average True Range (rolling mean of the true range) over ``period`` bars."""
    h, lo, c = _s(high), _s(low), _s(close)
    tr = pd.concat(
        [h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean().to_numpy(dtype=float)


def adx(high: _ArrayLike, low: _ArrayLike, close: _ArrayLike, *, period: int = 14) -> np.ndarray:
    """Average Directional Index — trend strength (>25 trending, <20 ranging)."""
    h, lo, c = _s(high), _s(low), _s(close)
    plus_dm = h.diff().clip(lower=0)
    minus_dm = (-lo.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0.0
    minus_dm[minus_dm < plus_dm] = 0.0
    tr = pd.concat(
        [h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1
    ).max(axis=1)
    atr_ = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return dx.ewm(span=period, adjust=False).mean().to_numpy(dtype=float)


def bb_width(close: _ArrayLike, *, period: int = 20, std_dev: float = 2.0) -> np.ndarray:
    """Bollinger-band width ``(2·std_dev·rolling_std) / rolling_mean`` — squeeze gauge."""
    c = _s(close)
    sma = c.rolling(period).mean()
    std = c.rolling(period).std()
    return ((2.0 * std_dev * std) / sma).to_numpy(dtype=float)


def classify_regime(
    *,
    vol_percentile: float,
    adx_value: float,
    hurst_value: float,
    trend_direction: int,
) -> dict[str, str]:
    """Map summary stats to a 4-quadrant regime label set.

    Parameters are point-in-time summaries (e.g. the latest ATR percentile,
    ADX, Hurst, and an EMA-slope sign in ``{-1, 0, +1}``). Returns
    ``volatility`` (low/normal/high), ``trend`` (trending/ranging/transitional),
    ``direction`` (up/down/neutral), ``mean_reversion``
    (mean_reverting/trending/random_walk), and a combined ``quadrant``.
    """
    volatility = "low" if vol_percentile < 0.30 else "high" if vol_percentile > 0.70 else "normal"
    trend = "trending" if adx_value > 25 else "ranging" if adx_value < 20 else "transitional"
    direction = "up" if trend_direction > 0 else "down" if trend_direction < 0 else "neutral"
    mean_reversion = (
        "mean_reverting" if hurst_value < 0.4
        else "trending" if hurst_value > 0.6
        else "random_walk"
    )
    return {
        "volatility": volatility,
        "trend": trend,
        "direction": direction,
        "mean_reversion": mean_reversion,
        "quadrant": f"{volatility}_vol_{trend}",
    }
