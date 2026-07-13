"""Mean-reversion / stationarity diagnostics — market-agnostic per-series stats.

These characterise a single 1-D series (a price, a spread, a factor's return
stream): is it mean-reverting, and if so how fast? They return scalars / small
dicts rather than same-shaped feature columns — they describe a series, they are
not cross-sectional factor values. Use them to decide *whether* a reversal /
pairs thesis holds before mining it, and to report half-life in the factor
report.

* :func:`hurst_exponent` — rescaled-range (R/S) exponent. H<0.5 mean-reverting,
  0.5 random walk, >0.5 trending.
* :func:`half_life` — AR(1) mean-reversion half-life in bars.
* :func:`adf_test` — Augmented Dickey-Fuller unit-root t-stat + approx p-value.
* :func:`variance_ratio` — Lo-MacKinlay variance ratio at horizon q.
* :func:`ou_params` — Ornstein-Uhlenbeck (theta, mu, sigma, half-life) via AR(1).

Pure numpy + scipy. Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
mean-reversion/scripts/mean_reversion_test.py.
"""
from __future__ import annotations

import numpy as np
from scipy import stats as _stats

_ArrayLike = np.ndarray | list

# MacKinnon approximate ADF critical values (constant, no trend, large n).
_ADF_CRIT = {0.01: -3.43, 0.05: -2.86, 0.10: -2.57}


def _f(x: _ArrayLike) -> np.ndarray:
    return np.asarray(x, dtype=float).ravel()


def hurst_exponent(series: _ArrayLike, *, min_window: int = 10) -> float:
    """Hurst exponent via rescaled range (R/S). Returns 0.5 if inconclusive.

    Raises ``ValueError`` if the series is shorter than ``2 * min_window``.
    """
    s = _f(series)
    n = s.size
    if n < 2 * min_window:
        raise ValueError(f"series too short ({n}); need >= {2 * min_window}")
    max_window = n // 2
    windows: list[int] = []
    w = min_window
    while w <= max_window:
        windows.append(w)
        w = max(w + 1, int(w * 1.5))
    log_n: list[float] = []
    log_rs: list[float] = []
    for w in windows:
        rs_block: list[float] = []
        for i in range(n // w):
            block = s[i * w : (i + 1) * w]
            dev = np.cumsum(block - block.mean())
            R = float(dev.max() - dev.min())
            S = float(np.std(block, ddof=1))
            if S > 1e-10:
                rs_block.append(R / S)
        if rs_block:
            log_n.append(np.log(w))
            log_rs.append(np.log(np.mean(rs_block)))
    if len(log_n) < 2:
        return 0.5
    return float(np.polyfit(log_n, log_rs, 1)[0])


def half_life(series: _ArrayLike) -> dict[str, float]:
    """Mean-reversion half-life (bars) from ``dX_t = a + b·X_{t-1} + e``.

    ``half_life = -ln 2 / ln(1 + b)`` when ``b < 0`` (mean-reverting); returns
    ``half_life = -1.0`` when ``b >= 0`` (not mean-reverting). Also returns the
    AR(1) ``beta``, the implied long-run mean ``mu``, and ``r_squared``.
    """
    s = _f(series)
    y = np.diff(s)
    x = s[:-1]
    X = np.column_stack([np.ones(x.size), x])
    alpha, beta = (float(v) for v in np.linalg.lstsq(X, y, rcond=None)[0])
    y_hat = X @ np.array([alpha, beta])
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if beta >= 0:
        return {"half_life": -1.0, "beta": beta, "mu": float(s.mean()), "r_squared": r2}
    return {
        "half_life": float(-np.log(2) / np.log(1 + beta)),
        "beta": beta,
        "mu": float(-alpha / beta),
        "r_squared": r2,
    }


def adf_test(series: _ArrayLike, *, max_lag: int = 1) -> dict[str, object]:
    """Augmented Dickey-Fuller unit-root test (constant, no trend).

    Returns the ``test_statistic`` (t-stat on the lagged level), an approximate
    ``p_value``, the level coefficient ``beta``, and a plain-language
    ``conclusion``. More negative t-stat = stronger evidence of stationarity
    (mean reversion).
    """
    s = _f(series)
    y = np.diff(s)
    x_lag = s[:-1]
    start = max_lag
    y_t = y[start:]
    regressors = [np.ones(y_t.size), x_lag[start:]]
    for lag in range(1, max_lag + 1):
        regressors.append(y[start - lag : y.size - lag])
    X = np.column_stack(regressors)
    coeffs = np.linalg.lstsq(X, y_t, rcond=None)[0]
    beta = float(coeffs[1])
    resid = y_t - X @ coeffs
    dof = y_t.size - coeffs.size
    sigma2 = float(np.sum(resid**2) / dof) if dof > 0 else np.nan
    se_beta = float(np.sqrt(sigma2 * np.linalg.inv(X.T @ X)[1, 1]))
    t_stat = beta / se_beta if se_beta > 0 else 0.0
    if t_stat < _ADF_CRIT[0.01]:
        p, concl = 0.005, "strongly stationary (p<0.01) — mean-reverting"
    elif t_stat < _ADF_CRIT[0.05]:
        p, concl = 0.03, "stationary (p<0.05) — likely mean-reverting"
    elif t_stat < _ADF_CRIT[0.10]:
        p, concl = 0.07, "weakly stationary (p<0.10) — possibly mean-reverting"
    else:
        p, concl = 0.20, "non-stationary (p>0.10) — NOT mean-reverting"
    return {
        "test_statistic": float(t_stat),
        "p_value": float(p),
        "beta": beta,
        "critical_values": dict(_ADF_CRIT),
        "conclusion": concl,
    }


def variance_ratio(series: _ArrayLike, *, q: int = 5) -> dict[str, float]:
    """Lo-MacKinlay variance ratio at horizon ``q``.

    VR<1 mean-reverting, ~1 random walk, >1 trending. Returns ``vr``, the
    homoskedastic ``z_stat`` and two-sided ``p_value``.
    """
    if q < 2:
        raise ValueError("q must be >= 2")
    log_p = np.log(_f(series))
    r1 = np.diff(log_p)
    n = r1.size
    var_1 = float(np.var(r1, ddof=1))
    if var_1 < 1e-15:
        return {"vr": 1.0, "z_stat": 0.0, "p_value": 1.0}
    r_q = log_p[q:] - log_p[:-q]
    var_q = float(np.var(r_q, ddof=1))
    vr = var_q / (q * var_1)
    z = (vr - 1.0) / np.sqrt(2.0 * (q - 1) / (3.0 * n))
    p = float(2.0 * (1.0 - _stats.norm.cdf(abs(z))))
    return {"vr": float(vr), "z_stat": float(z), "p_value": p}


def ou_params(series: _ArrayLike, *, dt: float = 1.0) -> dict[str, float]:
    """Ornstein-Uhlenbeck parameters from a discrete AR(1) fit.

    ``dX = theta·(mu - X)·dt + sigma·dW``. Returns ``theta`` (reversion speed),
    ``mu`` (long-run mean), ``sigma`` (vol), and ``half_life``. If the AR(1)
    slope is non-negative the series is not mean-reverting and ``theta=0``,
    ``half_life=-1``.
    """
    s = _f(series)
    y = np.diff(s)
    x = s[:-1]
    X = np.column_stack([np.ones(x.size), x])
    alpha, beta = (float(v) for v in np.linalg.lstsq(X, y, rcond=None)[0])
    if beta >= 0:
        return {
            "theta": 0.0,
            "mu": float(s.mean()),
            "sigma": float(np.std(y)),
            "half_life": -1.0,
        }
    theta = -np.log(1 + beta) / dt
    resid_std = float(np.std(y - (alpha + beta * x)))
    exp_term = 1.0 - np.exp(-2 * theta * dt)
    sigma = resid_std * np.sqrt(2 * theta / exp_term) if exp_term > 0 else resid_std
    return {
        "theta": float(theta),
        "mu": float(-alpha / beta),
        "sigma": float(sigma),
        "half_life": float(np.log(2) / theta) if theta > 0 else -1.0,
    }
