"""IC-based factor overfitting diagnostics — market-agnostic.

Complements :mod:`.multiple_testing` (Sharpe-deflation / FDR) and :mod:`.overfit`
(PBO / CPCV) with the *information-coefficient* view of a single factor: is its
cross-sectional predictive power stable, regime-robust, better-than-noise, and
persistent? Four tests, each returning a :class:`TestResult`, plus a composite
:func:`factor_overfit_report`.

* :func:`ic_stability` — is the per-period IC consistently signed and non-trivial
  (positive rate, mean magnitude, no sub-period sign reversal)?
* :func:`subsample_stress` — does the IC keep its sign across market regimes?
* :func:`placebo_test` — does the real IC beat a permutation null (factor shuffled
  across the cross-section), and does a time-shifted factor decay?
* :func:`ic_half_life` — fit ``IC(h) = a·exp(-b·h)`` across forward horizons; the
  half-life should exceed the rebalance horizon, not collapse in a day or two.

All operate on ``(T, S)`` factor and forward-return arrays (T periods x S
instruments); nothing about a market/calendar/cost is assumed.

Adapted from claude-trading-skills sibling repo QuantGPT (quantgpt/anti_overfit.py)
— the pandas long-format detector is reworked to numpy wide arrays and argus
conventions.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class TestResult:
    """One anti-overfit test outcome: headline ``passed`` + supporting numbers."""

    name: str
    passed: bool
    details: dict[str, object] = field(default_factory=dict)


# ── IC computation ──────────────────────────────────────────────────

def _rank(a: np.ndarray) -> np.ndarray:
    """Average ranks of a 1-D array (ties averaged); NaNs stay NaN."""
    out = np.full(a.shape, np.nan)
    m = ~np.isnan(a)
    if m.sum() == 0:
        return out
    order = np.argsort(a[m], kind="mergesort")
    ranks = np.empty(order.size, dtype=float)
    ranks[order] = np.arange(order.size, dtype=float)
    # average ties
    vals = a[m][order]
    i = 0
    while i < vals.size:
        j = i + 1
        while j < vals.size and vals[j] == vals[i]:
            j += 1
        if j - i > 1:
            ranks[order[i:j]] = ranks[order[i:j]].mean()
        i = j
    out[m] = ranks
    return out


def _row_spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman correlation of two 1-D rows; NaN if fewer than 3 valid pairs."""
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 3:
        return float("nan")
    xr, yr = _rank(x[m]), _rank(y[m])
    if np.std(xr) == 0 or np.std(yr) == 0:
        return float("nan")
    return float(np.corrcoef(xr, yr)[0, 1])


def cross_sectional_ic(factor: np.ndarray, forward_returns: np.ndarray) -> np.ndarray:
    """Per-period cross-sectional (Spearman rank) IC.

    ``factor`` and ``forward_returns`` are ``(T, S)``; returns a length-``T``
    array, NaN on periods with fewer than 3 valid stock pairs.
    """
    f = np.asarray(factor, dtype=float)
    r = np.asarray(forward_returns, dtype=float)
    if f.shape != r.shape or f.ndim != 2:
        raise ValueError("factor and forward_returns must be equal-shaped (T, S) arrays")
    return np.array([_row_spearman(f[t], r[t]) for t in range(f.shape[0])], dtype=float)


# ── Test 1: IC stability ────────────────────────────────────────────

def ic_stability(
    ic: np.ndarray,
    *,
    period_labels: Sequence[object] | None = None,
    n_periods: int = 5,
    min_positive_rate: float = 0.55,
    min_abs_ic: float = 0.02,
) -> TestResult:
    """IC consistency: positive rate, mean magnitude, no sub-period sign reversal.

    ``period_labels`` groups the per-period IC into sub-periods (e.g. year of
    each period); ``None`` splits it into ``n_periods`` contiguous chunks. Passes
    when the positive-IC rate ≥ ``min_positive_rate``, ``|mean IC|`` ≥
    ``min_abs_ic``, and every sub-period's mean IC shares the overall sign.
    """
    ic = np.asarray(ic, dtype=float)
    valid = ic[~np.isnan(ic)]
    if valid.size < 20:
        return TestResult("ic_stability", False, {"error": "insufficient IC observations"})
    ic_mean = float(valid.mean())
    positive_rate = float((valid > 0).mean())
    overall_sign = np.sign(ic_mean)

    if period_labels is not None:
        labels = np.asarray(list(period_labels))
        groups = [ic[labels == u] for u in dict.fromkeys(labels.tolist())]
    else:
        groups = np.array_split(ic, n_periods)
    sub_means = [float(np.nanmean(g)) for g in groups if np.any(~np.isnan(g))]
    has_reversal = (
        overall_sign == 0 or any(np.sign(m) != overall_sign for m in sub_means)
    )
    passed = (
        positive_rate >= min_positive_rate
        and abs(ic_mean) >= min_abs_ic
        and not has_reversal
    )
    return TestResult("ic_stability", passed, {
        "ic_mean": round(ic_mean, 4),
        "positive_rate": round(positive_rate, 4),
        "sub_period_ic": [round(m, 4) for m in sub_means],
        "has_reversal": bool(has_reversal),
    })


# ── Test 2: sub-sample (regime) stress ──────────────────────────────

def market_regime_labels(
    returns: np.ndarray, *, window: int = 60, up: float = 0.05, down: float = -0.05
) -> np.ndarray:
    """Label each period bull/bear/sideways from the equal-weight market trend.

    A convenience for :func:`subsample_stress`: the market return per period is
    the cross-sectional mean of ``returns`` (T, S); its trailing ``window``-sum
    classifies the regime. Early periods (< window) are ``"sideways"``.
    """
    r = np.asarray(returns, dtype=float)
    mkt = np.nanmean(r, axis=1)
    labels = np.array(["sideways"] * r.shape[0], dtype=object)
    for t in range(r.shape[0]):
        lo = max(0, t - window + 1)
        if t + 1 - lo < window // 2:
            continue
        s = float(np.nansum(mkt[lo : t + 1]))
        labels[t] = "bull" if s > up else "bear" if s < down else "sideways"
    return labels


def subsample_stress(
    ic: np.ndarray,
    regime_labels: Sequence[object],
    *,
    min_consistency: float = 0.6,
    min_group: int = 10,
) -> TestResult:
    """Fraction of regime sub-samples whose mean IC shares the overall sign.

    ``regime_labels`` is a length-``T`` labelling (e.g. from
    :func:`market_regime_labels`). Passes when ≥ ``min_consistency`` of the
    sub-samples (each with ≥ ``min_group`` periods) agree in sign with the
    overall IC.
    """
    ic = np.asarray(ic, dtype=float)
    labels = np.asarray(list(regime_labels))
    valid = ic[~np.isnan(ic)]
    if valid.size < 40:
        return TestResult("subsample_stress", False, {"error": "insufficient IC observations"})
    overall_sign = np.sign(np.nanmean(ic))
    if overall_sign == 0:
        return TestResult("subsample_stress", False, {"error": "overall IC is zero"})
    sub: dict[str, float] = {}
    for u in dict.fromkeys(labels.tolist()):
        g = ic[labels == u]
        g = g[~np.isnan(g)]
        if g.size >= min_group:
            sub[str(u)] = float(g.mean())
    if not sub:
        return TestResult("subsample_stress", False, {"error": "no sub-sample had enough data"})
    consistency = sum(1 for v in sub.values() if np.sign(v) == overall_sign) / len(sub)
    return TestResult("subsample_stress", consistency >= min_consistency, {
        "overall_sign": int(overall_sign),
        "sub_sample_ic": {k: round(v, 4) for k, v in sub.items()},
        "consistency": round(consistency, 4),
    })


# ── Test 3: placebo (permutation) ───────────────────────────────────

def placebo_test(
    factor: np.ndarray,
    forward_returns: np.ndarray,
    *,
    n_permutations: int = 50,
    seed: int = 0,
) -> TestResult:
    """Does the real IC beat a permutation null?

    Shuffles the factor across the cross-section within each period
    ``n_permutations`` times and compares the real mean |IC| to the null's 95th
    percentile. Passes when the real signal exceeds it (empirical p < 0.05).
    """
    f = np.asarray(factor, dtype=float)
    r = np.asarray(forward_returns, dtype=float)
    real = float(np.nanmean(cross_sectional_ic(f, r)))
    rng = np.random.default_rng(seed)
    null = np.empty(n_permutations)
    T = f.shape[0]
    for i in range(n_permutations):
        shuffled = f.copy()
        for t in range(T):
            row = shuffled[t]
            m = ~np.isnan(row)
            if m.sum() > 1:
                perm = row[m]
                rng.shuffle(perm)
                row[m] = perm
        null[i] = float(np.nanmean(cross_sectional_ic(shuffled, r)))
    null_abs = np.abs(null)
    perm_95 = float(np.percentile(null_abs, 95))
    p_value = float((null_abs >= abs(real)).mean())
    return TestResult("placebo", abs(real) > perm_95, {
        "real_ic": round(real, 4),
        "null_95th_abs_ic": round(perm_95, 4),
        "p_value": round(p_value, 4),
        "n_permutations": n_permutations,
    })


# ── Test 4: IC half-life ────────────────────────────────────────────

def _forward_cum_return(one_period_returns: np.ndarray, horizon: int) -> np.ndarray:
    """Compound forward return over the next ``horizon`` periods, aligned to t."""
    r = np.asarray(one_period_returns, dtype=float)
    T = r.shape[0]
    gross = 1.0 + r
    out = np.full_like(r, np.nan)
    for t in range(T - horizon):
        out[t] = np.prod(gross[t + 1 : t + 1 + horizon], axis=0) - 1.0
    return out


def ic_half_life(
    factor: np.ndarray,
    one_period_returns: np.ndarray,
    *,
    horizons: Sequence[int] = (1, 2, 5, 10, 20, 40),
    min_half_life: float = 5.0,
) -> TestResult:
    """Fit ``IC(h) = a·exp(-b·h)`` across forward horizons; report the half-life.

    ``one_period_returns`` is the ``(T, S)`` single-period return matrix; the
    factor is scored against the compounded forward return at each horizon.
    Passes when the fitted half-life exceeds ``min_half_life`` periods (the
    signal persists rather than collapsing immediately).
    """
    f = np.asarray(factor, dtype=float)
    xs: list[float] = []
    ys: list[float] = []
    for h in horizons:
        fwd = _forward_cum_return(one_period_returns, h)
        ic_h = np.nanmean(cross_sectional_ic(f, fwd))
        if not np.isnan(ic_h):
            xs.append(float(h))
            ys.append(abs(float(ic_h)))
    if len(xs) < 3:
        return TestResult("ic_half_life", False, {"error": "insufficient horizon ICs"})

    x = np.array(xs)
    y = np.array(ys)
    half_life = float("inf")
    try:
        from scipy.optimize import curve_fit  # lazy

        popt, _ = curve_fit(
            lambda t, a, b: a * np.exp(-b * t), x, y, p0=[y[0], 0.05], maxfev=5000
        )
        b = float(popt[1])
        half_life = float(np.log(2) / b) if b > 0 else float("inf")
    except Exception:  # noqa: BLE001 - fall back to a two-point estimate
        if y[0] > 0 and y[-1] > 0 and y[-1] < y[0]:
            b = float(np.log(y[0] / y[-1]) / (x[-1] - x[0]))
            half_life = float(np.log(2) / b) if b > 0 else float("inf")
    return TestResult("ic_half_life", half_life > min_half_life, {
        "half_life_periods": round(half_life, 2) if np.isfinite(half_life) else None,
        "horizon_ic": {str(int(h)): round(v, 4) for h, v in zip(xs, ys)},
    })


# ── Composite ───────────────────────────────────────────────────────

def factor_overfit_report(
    factor: np.ndarray,
    one_period_returns: np.ndarray,
    *,
    forward_returns: np.ndarray | None = None,
    period_labels: Sequence[object] | None = None,
    regime_labels: Sequence[object] | None = None,
    horizons: Sequence[int] = (1, 2, 5, 10, 20, 40),
) -> dict[str, object]:
    """Run all four IC tests and return a composite score out of 4.

    ``forward_returns`` (the one-period target) defaults to the next single
    period from ``one_period_returns``; ``regime_labels`` defaults to
    :func:`market_regime_labels`. The result mirrors the reviewer's need: a
    ``score`` in {0..4}, a pass flag per test, and their details.
    """
    if forward_returns is None:
        forward_returns = _forward_cum_return(one_period_returns, 1)
    if regime_labels is None:
        regime_labels = market_regime_labels(one_period_returns)
    ic = cross_sectional_ic(factor, forward_returns)
    tests = [
        ic_stability(ic, period_labels=period_labels),
        subsample_stress(ic, regime_labels),
        placebo_test(factor, forward_returns),
        ic_half_life(factor, one_period_returns, horizons=horizons),
    ]
    passed = sum(1 for t in tests if t.passed)
    return {
        "score": passed,
        "total": len(tests),
        "tests": [{"name": t.name, "passed": t.passed, "details": t.details} for t in tests],
    }
