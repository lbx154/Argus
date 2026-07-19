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


def _allocate_capped(values: np.ndarray, *, target: float, cap: float) -> np.ndarray:
    """Proportionally allocate one side of a book with a hard per-name cap."""
    out = np.zeros_like(values, dtype=float)
    active = np.flatnonzero(values > 0)
    tolerance = 1e-12
    if len(active) * cap < target - tolerance:
        raise ValueError(
            f"max_weight={cap:g} is infeasible for {len(active)} names on one side "
            f"of a dollar-neutral book"
        )

    remaining = target
    while len(active):
        strengths = values[active]
        proposed = remaining * strengths / strengths.sum()
        over_cap = proposed > cap
        if not np.any(over_cap):
            out[active] = proposed
            return out
        capped = active[over_cap]
        out[capped] = cap
        remaining -= len(capped) * cap
        active = active[~over_cap]

    if remaining > tolerance:
        raise ValueError("unable to allocate capped portfolio weights")
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
    allocates gross 0.5 to each side while enforcing ``max_weight``. Names with
    NaN score get zero weight. Raises ``ValueError`` when a requested cap is too
    small for the available long or short breadth.
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
    positive = np.clip(s, 0.0, None)
    negative = np.clip(-s, 0.0, None)
    if positive.sum() <= 0 or negative.sum() <= 0:
        return np.zeros(len(s))

    cap: float | None = None
    if max_weight != 0:
        cap = float(max_weight)
        if not np.isfinite(cap) or cap < 0:
            raise ValueError("max_weight must be finite and non-negative")

    if cap is None:
        long_weights = 0.5 * positive / positive.sum()
        short_weights = 0.5 * negative / negative.sum()
    else:
        long_weights = _allocate_capped(positive, target=0.5, cap=cap)
        short_weights = _allocate_capped(negative, target=0.5, cap=cap)
    return long_weights - short_weights


def book_returns(weights: list[np.ndarray], fwd_rets: list[np.ndarray], *, cost: float) -> np.ndarray:
    """Net return per rebalance: ``w·r − turnover·cost`` (turnover vs prior book)."""
    if len(weights) != len(fwd_rets):
        raise ValueError(
            "weights and fwd_rets must contain the same number of rebalances "
            f"({len(weights)} != {len(fwd_rets)})"
        )
    out = np.zeros(len(weights))
    prev: np.ndarray | None = None
    for i, (w, r) in enumerate(zip(weights, fwd_rets)):
        current = np.asarray(w, dtype=float)
        returns = np.asarray(r, dtype=float)
        if current.shape != returns.shape:
            raise ValueError(
                f"weights and forward returns differ at rebalance {i}: "
                f"{current.shape} != {returns.shape}"
            )
        if prev is not None and current.shape != prev.shape:
            raise ValueError(
                f"weight shape changed at rebalance {i}: {prev.shape} != {current.shape}"
            )
        gross = float(np.nansum(current * returns))
        turn = 1.0 if prev is None else float(np.abs(current - prev).sum() / 2.0)
        out[i] = gross - turn * cost
        prev = current
    return out


def sharpe_maxdd(net: np.ndarray, *, periods_per_year: float) -> tuple[float, float]:
    """Annualised Sharpe and max drawdown of a per-period net-return series."""
    net = np.asarray(net, dtype=float)
    sd = net.std(ddof=1)
    sharpe = float(net.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0
    eq: np.ndarray = np.cumprod(1.0 + net)
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min()) if len(eq) else 0.0
    return sharpe, dd
