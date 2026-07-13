"""Portfolio construction — turn a cross-sectional score into tradable weights.

A predictive score (higher = higher expected return) is not a portfolio. This
turns one day's cross-section of scores into **dollar-neutral** weights, using the
levers that convert a real-but-weak signal into a better risk-adjusted return
without needing a stronger signal:

* **full-breadth signal weighting** — weight by the (centred) rank of every name,
  not just a top/bottom quintile, so the whole cross-section's information is used
  (the fundamental law: IR ≈ IC·√breadth);
* **size neutralisation** — residualise the score against a size proxy so the book
  is not an unintended small/large-cap bet;
* **inverse-vol risk scaling** — divide by each name's recent volatility so no
  single volatile name dominates the book's risk (risk-parity flavour);
* **per-name caps** — bound concentration.

Weights are numpy-only and NaN-safe; :func:`book_returns` scores a sequence of
rebalances into a net-of-cost return series.
"""
from __future__ import annotations

import numpy as np


def _rank_center(s: np.ndarray) -> np.ndarray:
    """Percentile rank centred to ``[-0.5, 0.5]`` (NaN-safe)."""
    out = np.full(len(s), np.nan)
    m = ~np.isnan(s)
    n = int(m.sum())
    if n < 2:
        return out
    ranks = np.argsort(np.argsort(s[m])).astype(float)
    out[m] = ranks / (n - 1) - 0.5
    return out


def _residualize(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Cross-sectional OLS residual of ``y`` on ``[1, x]`` (NaN-safe on overlap)."""
    out = np.array(y, dtype=float)
    m = ~(np.isnan(y) | np.isnan(x))
    if int(m.sum()) < 3:
        return out
    X = np.column_stack([np.ones(int(m.sum())), x[m]])
    beta, *_ = np.linalg.lstsq(X, y[m], rcond=None)
    out[m] = y[m] - X @ beta
    return out


def to_weights(
    scores: np.ndarray,
    *,
    size: np.ndarray | None = None,
    vol: np.ndarray | None = None,
    neutralize_size: bool = False,
    inv_vol: bool = False,
    max_weight: float = 0.03,
) -> np.ndarray:
    """One day's scores → dollar-neutral weights (gross ``Σ|w| = 1``).

    Rank-centres the scores (full breadth), optionally residualises against
    ``size`` and/or divides by ``vol`` (inverse-vol), demeans to dollar-neutral,
    normalises to gross 1, then caps per-name at ``max_weight`` and renormalises.
    Names with NaN score get zero weight.
    """
    s = _rank_center(np.asarray(scores, dtype=float))
    if neutralize_size and size is not None:
        s = _residualize(s, np.asarray(size, dtype=float))
    if inv_vol and vol is not None:
        v = np.asarray(vol, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            s = s / np.where(v > 0, v, np.nan)
    s = s - np.nanmean(s)                       # dollar-neutral
    s = np.nan_to_num(s, nan=0.0)
    g = np.abs(s).sum()
    if g <= 0:
        return np.zeros(len(s))
    w = s / g
    if max_weight and max_weight > 0:
        w = np.clip(w, -max_weight, max_weight)
        g2 = np.abs(w).sum()
        if g2 > 0:
            w = w / g2
    return w


def book_returns(weights: list[np.ndarray], fwd_rets: list[np.ndarray], *, cost: float) -> np.ndarray:
    """Net return per rebalance: ``w·r − turnover·cost`` (turnover vs prior book)."""
    out = np.zeros(len(weights))
    prev: np.ndarray | None = None
    for i, (w, r) in enumerate(zip(weights, fwd_rets)):
        gross = float(np.nansum(w * r))
        turn = 1.0 if prev is None else float(np.abs(w - prev).sum() / 2.0)
        out[i] = gross - turn * cost
        prev = w
    return out


def sharpe_maxdd(net: np.ndarray, *, periods_per_year: float) -> tuple[float, float]:
    """Annualised Sharpe and max drawdown of a per-period net-return series."""
    net = np.asarray(net, dtype=float)
    sd = net.std(ddof=1)
    sharpe = float(net.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0
    eq = np.cumprod(1.0 + net)
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min()) if len(eq) else 0.0
    return sharpe, dd
