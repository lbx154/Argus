"""Portfolio performance metrics — market-agnostic, numpy-only.

Return, risk, and risk-adjusted statistics computed from a return series or an
equity curve. Every annualised metric takes ``periods_per_year`` explicitly
(default 252, the common trading-day count) — there is no hardcoded calendar,
so a daily-crypto caller passes 365, an hourly caller passes the bars-per-year
of its own clock, and A-share / futures callers pass their session count. No
market, cost, or tradability assumption is baked in.

Inputs are array-likes (list / ``np.ndarray`` / ``pd.Series``); scalar metrics
return ``float`` and series metrics return ``np.ndarray``. Standard deviations
use the sample estimator (``ddof=1``) to match the usual finance convention.

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
portfolio-analytics/scripts/analyze_portfolio.py — the calendar-day CAGR is
reworked to a periods-based form so it needs no datetime index.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

_ArrayLike = Sequence[float] | np.ndarray


def _clean(x: _ArrayLike) -> np.ndarray:
    """Coerce to a 1-D float array with NaNs dropped."""
    arr = np.asarray(x, dtype=float).ravel()
    return arr[~np.isnan(arr)]


def _std(x: np.ndarray) -> float:
    """Sample standard deviation (ddof=1); 0.0 for fewer than 2 points."""
    return float(np.std(x, ddof=1)) if x.size >= 2 else 0.0


# ── Return / risk ───────────────────────────────────────────────────

def cagr(equity: _ArrayLike, *, periods_per_year: int = 252) -> float:
    """Compound annual growth rate from an equity curve.

    Periods-based (no datetime index needed): with ``n`` points there are
    ``n - 1`` compounding steps, so the horizon in years is
    ``(n - 1) / periods_per_year``. Returns 0.0 for a degenerate curve.
    """
    eq = np.asarray(equity, dtype=float).ravel()
    if eq.size < 2 or eq[0] <= 0:
        return 0.0
    years = (eq.size - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return float((eq[-1] / eq[0]) ** (1.0 / years) - 1.0)


def annualized_volatility(returns: _ArrayLike, *, periods_per_year: int = 252) -> float:
    """Annualised standard deviation of the return series."""
    return float(_std(_clean(returns)) * np.sqrt(periods_per_year))


def historical_var(returns: _ArrayLike, *, confidence: float = 0.95) -> float:
    """Historical Value-at-Risk as a positive loss magnitude."""
    r = _clean(returns)
    if r.size == 0:
        return 0.0
    return float(-np.percentile(r, (1.0 - confidence) * 100.0))


def historical_cvar(returns: _ArrayLike, *, confidence: float = 0.95) -> float:
    """Historical Conditional VaR (expected shortfall) as a positive magnitude."""
    r = _clean(returns)
    if r.size == 0:
        return 0.0
    var = historical_var(r, confidence=confidence)
    tail = r[r <= -var]
    return float(-tail.mean()) if tail.size else var


def drawdown_series(equity: _ArrayLike) -> np.ndarray:
    """Per-point drawdown ``(equity - running_peak) / running_peak`` (<= 0)."""
    eq = np.asarray(equity, dtype=float).ravel()
    if eq.size == 0:
        return np.zeros(0, dtype=float)
    peak = np.maximum.accumulate(eq)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, (eq - peak) / peak, 0.0)
    return dd


def max_drawdown(equity: _ArrayLike) -> float:
    """Maximum peak-to-trough decline as a negative decimal (e.g. -0.15)."""
    dd = drawdown_series(equity)
    return float(dd.min()) if dd.size else 0.0


# ── Risk-adjusted ratios ────────────────────────────────────────────

def sharpe_ratio(
    returns: _ArrayLike, *, rf: float = 0.0, periods_per_year: int = 252
) -> float:
    """Annualised Sharpe ratio; 0.0 when excess-return volatility is zero."""
    excess = _clean(returns) - rf
    sd = _std(excess)
    if sd == 0.0:
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods_per_year))


def sortino_ratio(
    returns: _ArrayLike, *, rf: float = 0.0, periods_per_year: int = 252
) -> float:
    """Annualised Sortino ratio (downside deviation only).

    ``inf`` when there is no downside and mean excess is positive; 0.0 when
    there is no downside and mean excess is non-positive.
    """
    excess = _clean(returns) - rf
    downside = excess[excess < 0]
    dd = _std(downside)
    if downside.size == 0 or dd == 0.0:
        return float("inf") if excess.mean() > 0 else 0.0
    return float(excess.mean() / dd * np.sqrt(periods_per_year))


def calmar_ratio(equity: _ArrayLike, *, periods_per_year: int = 252) -> float:
    """Calmar ratio: CAGR / |max drawdown|."""
    annual = cagr(equity, periods_per_year=periods_per_year)
    mdd = abs(max_drawdown(equity))
    if mdd == 0.0:
        return float("inf") if annual > 0 else 0.0
    return float(annual / mdd)


def information_ratio(
    returns: _ArrayLike,
    benchmark_returns: _ArrayLike,
    *,
    periods_per_year: int = 252,
) -> float:
    """Annualised information ratio: mean active return / tracking error.

    The two series are aligned by position and must have equal length.
    """
    r = np.asarray(returns, dtype=float).ravel()
    b = np.asarray(benchmark_returns, dtype=float).ravel()
    if r.shape != b.shape:
        raise ValueError(f"length mismatch: returns {r.shape} vs benchmark {b.shape}")
    active = r - b
    active = active[~np.isnan(active)]
    sd = _std(active)
    if sd == 0.0:
        return 0.0
    return float(active.mean() / sd * np.sqrt(periods_per_year))


# ── Trade-level ─────────────────────────────────────────────────────

def trade_statistics(pnl: _ArrayLike) -> dict[str, float]:
    """Win rate, average win/loss, profit factor, expectancy from trade PnLs.

    ``profit_factor`` is ``inf`` when there are wins but no losses.
    """
    p = np.asarray(pnl, dtype=float).ravel()
    p = p[~np.isnan(p)]
    total = int(p.size)
    wins = p[p > 0]
    losses = p[p < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "total_trades": total,
        "win_rate": float(wins.size / total) if total else 0.0,
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "largest_win": float(wins.max()) if wins.size else 0.0,
        "largest_loss": float(losses.min()) if losses.size else 0.0,
        "profit_factor": profit_factor,
        "expectancy": float(p.mean()) if total else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }
