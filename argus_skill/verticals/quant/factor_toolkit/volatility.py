"""Volatility estimators — market-agnostic rolling risk features.

Five realized-volatility estimators over a price / OHLC array shaped ``(T,)`` or
``(T, S)``. Each returns a same-shaped array of *annualised* volatility using an
explicit ``periods_per_year`` (default 252) — no hardcoded 365/crypto calendar.
Higher-information estimators (Parkinson, Garman-Klass) use the intrabar range
and are lower-variance than close-to-close when OHLC is trustworthy.

Rolling windows use pandas internally (a confirmed dependency); inputs/outputs
are plain numpy so the results drop straight into :mod:`.builder`.

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
volatility-modeling/scripts/estimate_volatility.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_ArrayLike = np.ndarray | list


def _frame(x: _ArrayLike) -> tuple[pd.DataFrame, bool]:
    """Return ``(2-D DataFrame, was_1d)`` — 1-D inputs become a single column."""
    arr = np.asarray(x, dtype=float)
    if arr.ndim == 1:
        return pd.DataFrame(arr.reshape(-1, 1)), True
    if arr.ndim == 2:
        return pd.DataFrame(arr), False
    raise ValueError("input must be 1-D (T,) or 2-D (T, S)")


def _restore(df: pd.DataFrame, was_1d: bool) -> np.ndarray:
    out = df.to_numpy(dtype=float)
    return out[:, 0] if was_1d else out


def realized_vol(
    close: _ArrayLike, *, window: int, periods_per_year: int = 252
) -> np.ndarray:
    """Annualised close-to-close realized volatility (rolling std of log returns)."""
    df, was_1d = _frame(close)
    log_ret = np.log(df / df.shift(1))
    vol = log_ret.rolling(window).std(ddof=1) * np.sqrt(periods_per_year)
    return _restore(vol, was_1d)


def parkinson_vol(
    high: _ArrayLike, low: _ArrayLike, *, window: int, periods_per_year: int = 252
) -> np.ndarray:
    """Annualised Parkinson high-low range volatility."""
    hi, was_1d = _frame(high)
    lo, _ = _frame(low)
    hl_sq = np.log(hi / lo) ** 2
    factor = 1.0 / (4.0 * np.log(2.0))
    vol = np.sqrt(hl_sq.rolling(window).mean() * factor) * np.sqrt(periods_per_year)
    return _restore(vol, was_1d)


def garman_klass_vol(
    open_: _ArrayLike,
    high: _ArrayLike,
    low: _ArrayLike,
    close: _ArrayLike,
    *,
    window: int,
    periods_per_year: int = 252,
) -> np.ndarray:
    """Annualised Garman-Klass OHLC volatility (uses open, high, low, close)."""
    op, was_1d = _frame(open_)
    hi, _ = _frame(high)
    lo, _ = _frame(low)
    cl, _ = _frame(close)
    log_hl = np.log(hi / lo)
    log_co = np.log(cl / op)
    gk = 0.5 * log_hl**2 - (2.0 * np.log(2.0) - 1.0) * log_co**2
    vol = np.sqrt(gk.rolling(window).mean().clip(lower=0) * periods_per_year)
    return _restore(vol, was_1d)


def ewma_vol(
    close: _ArrayLike, *, lam: float = 0.94, periods_per_year: int = 252
) -> np.ndarray:
    """Annualised RiskMetrics EWMA volatility (decay ``lam``, default 0.94)."""
    if not 0.0 < lam < 1.0:
        raise ValueError("lam must be in (0, 1)")
    df, was_1d = _frame(close)
    log_ret = np.log(df / df.shift(1))
    ewvar = (log_ret**2).ewm(alpha=1.0 - lam, adjust=False).mean()
    vol = np.sqrt(ewvar) * np.sqrt(periods_per_year)
    return _restore(vol, was_1d)
