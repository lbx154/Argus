"""Price-derived factor features — market-agnostic, vectorised numpy.

Each function takes a price / OHLC array shaped ``(T,)`` (one series) or
``(T, S)`` (a cross-section of ``S`` instruments over ``T`` bars) and returns a
same-shaped array of the feature value, using only information at or before each
bar (no look-ahead). Warm-up positions are ``NaN``. These are the time-varying
signals that become factor columns in :mod:`.builder`; per-series *diagnostics*
(Hurst, half-life, ...) live in :mod:`.statistical`.

Nothing here assumes a calendar, cost, or tradability rule — those belong in an
``integrations/<market>/`` package.

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
feature-engineering/scripts/build_features.py.
"""
from __future__ import annotations

import numpy as np

_ArrayLike = np.ndarray | list


def _f(x: _ArrayLike) -> np.ndarray:
    return np.asarray(x, dtype=float)


def momentum(close: _ArrayLike, window: int) -> np.ndarray:
    """``close[t] / close[t-window] - 1`` — trailing return over ``window`` bars."""
    if window < 1:
        raise ValueError("window must be >= 1")
    c = _f(close)
    out = np.full_like(c, np.nan)
    out[window:] = c[window:] / c[:-window] - 1.0
    return out


def reversal(close: _ArrayLike, window: int) -> np.ndarray:
    """Short-horizon reversal factor: the negative of :func:`momentum`.

    High recent return → low factor value, encoding the reversal thesis (recent
    winners underperform). Pair with a positive ``direction`` in the FactorSpec.
    """
    return -momentum(close, window)


def acceleration(close: _ArrayLike, window: int = 5, lag: int = 5) -> np.ndarray:
    """Change in short-horizon momentum: ``mom_w[t] - mom_w[t-lag]``."""
    m = momentum(close, window)
    out = np.full_like(m, np.nan)
    out[lag:] = m[lag:] - m[:-lag]
    return out


def log_return(close: _ArrayLike) -> np.ndarray:
    """One-bar log return ``ln(close[t] / close[t-1])`` (first bar NaN)."""
    c = _f(close)
    out = np.full_like(c, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.log(c[1:] / c[:-1])
    return out


def high_low_range(high: _ArrayLike, low: _ArrayLike, close: _ArrayLike) -> np.ndarray:
    """Intrabar range as a fraction of close: ``(high - low) / close``."""
    h, lo, c = _f(high), _f(low), _f(close)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (h - lo) / c


def close_position(high: _ArrayLike, low: _ArrayLike, close: _ArrayLike) -> np.ndarray:
    """Where the close sits in the bar range: ``(close - low) / (high - low)``.

    Near 1.0 = closed at the high (buying pressure), near 0.0 = at the low.
    Degenerate zero-range bars map to 0.5.
    """
    h, lo, c = _f(high), _f(low), _f(close)
    rng = h - lo
    with np.errstate(divide="ignore", invalid="ignore"):
        pos = np.where(rng > 0, (c - lo) / rng, 0.5)
    return pos


def gap(open_: _ArrayLike, close: _ArrayLike) -> np.ndarray:
    """Overnight gap ``open[t] / close[t-1] - 1`` (first bar NaN)."""
    o, c = _f(open_), _f(close)
    out = np.full_like(o, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = o[1:] / c[:-1] - 1.0
    return out
