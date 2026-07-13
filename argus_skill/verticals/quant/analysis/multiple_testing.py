"""Multiple-testing accounting for factor research.

Two complementary tools:

* :func:`deflated_sharpe_ratio` (Bailey & López de Prado, 2014) computes the
  probability that an *observed* Sharpe ratio survives once the search
  breadth and the non-normality of the returns are accounted for. The
  reviewer reads this as the cardinal multiple-testing haircut for the
  headline strategy.

* :func:`bh_fdr` runs Benjamini-Hochberg false-discovery-rate control over a
  vector of per-factor p-values, returning the rejection mask. Used when the
  search produced many marginal factors and the question is "how many of
  these are likely real" — the FDR analog of the deflated Sharpe.

* :func:`haircut_sharpe` is the simple Bonferroni-style fallback: divide the
  Sharpe by ``sqrt(log(N_trials))``. Conservative; useful when distributional
  assumptions for the deflated formula are uncomfortable.

All three are pure numpy. No fancy distribution code: the deflated Sharpe
uses an analytic standard-normal CDF approximation (no scipy), accurate to
~1e-7 — plenty for haircut purposes.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


# Abramowitz & Stegun 26.2.17 — standard-normal CDF, max error ~7.5e-8.
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe_ratio(
    *,
    observed_sharpe: float,
    n_trials: int,
    sample_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability that the *true* Sharpe is positive given the search size.

    Implements the closed-form deflated Sharpe ratio. ``observed_sharpe`` is
    the **per-period** Sharpe of the headline strategy (in the same units as
    one observation in ``sample_length`` — daily SR over daily returns,
    monthly over monthly, etc.); ``sample_length`` is the number of return
    observations behind it; ``n_trials`` is the independent-trial count
    behind the maximum (read from the search ledger). ``skewness`` and
    ``kurtosis`` describe the strategy's return distribution
    (kurtosis = 3 for normal). Pass ``observed_sharpe = annualised_SR /
    sqrt(periods_per_year)`` if the headline number is annualised.

    Returns a probability in [0, 1]. A common decision rule: report a
    strategy as "passing the haircut" only when this probability exceeds
    0.95.

    Reference: Bailey & López de Prado (2014), "The Deflated Sharpe Ratio".
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if sample_length < 4:
        raise ValueError("sample_length must be >= 4")
    # Expected maximum of N standard normals (Sidák / Mertens approximation).
    # n_trials == 1 is the degenerate "no search" case: no haircut.
    if n_trials == 1:
        max_z = 0.0
    else:
        euler_mascheroni = 0.5772156649
        max_z = (
            (1.0 - euler_mascheroni) * _inv_phi(1.0 - 1.0 / n_trials)
            + euler_mascheroni * _inv_phi(1.0 - 1.0 / (n_trials * math.e))
        )
    sr_zero = max_z / math.sqrt(sample_length)
    denom = math.sqrt(
        max(
            1.0
            - skewness * observed_sharpe
            + (kurtosis - 1.0) / 4.0 * observed_sharpe * observed_sharpe,
            1e-12,
        )
    )
    z = (observed_sharpe - sr_zero) * math.sqrt(sample_length - 1) / denom
    return _phi(z)


def _inv_phi(p: float) -> float:
    """Inverse standard-normal CDF (Beasley-Springer-Moro). No scipy."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0,1), got {p!r}")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(
        ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
    ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def haircut_sharpe(*, observed_sharpe: float, n_trials: int) -> float:
    """Bonferroni-flavoured Sharpe haircut: SR / sqrt(log(N_trials)).

    Conservative and assumption-light. Useful as a sanity floor against the
    deflated Sharpe; if even this haircut still leaves the strategy positive
    by a wide margin, the headline is robust to any reasonable multiple-test
    correction.
    """
    if n_trials < 2:
        return float(observed_sharpe)
    return float(observed_sharpe / math.sqrt(math.log(n_trials)))


def effective_num_trials(scores: np.ndarray) -> float:
    """Effective number of *independent* trials from a candidate score matrix.

    When a model/factor search evaluates many correlated candidates, counting
    raw trials over-penalises: two near-identical models are ~one independent
    look. Given ``scores`` shaped ``(n_obs, n_candidates)`` (each column a
    candidate's prediction/return series), returns the participation-ratio of the
    correlation matrix eigenvalues, ``(Σλ)² / Σλ²`` — 1 when all candidates are
    perfectly correlated, ``n_candidates`` when orthogonal. Feed this (not the raw
    count) as ``n_trials`` to :func:`deflated_sharpe_ratio` / :func:`haircut_sharpe`
    so the multiple-testing haircut reflects the search's real breadth.
    """
    S = np.asarray(scores, dtype=float)
    if S.ndim != 2 or S.shape[1] < 1:
        raise ValueError("scores must be (n_obs, n_candidates)")
    if S.shape[1] == 1:
        return 1.0
    # correlation across candidates, NaN-robust
    S = S - np.nanmean(S, axis=0, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(np.nan_to_num(S, nan=0.0), rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0)
    eig = np.linalg.eigvalsh(corr)
    eig = np.clip(eig, 0.0, None)
    denom = float((eig ** 2).sum())
    if denom <= 0:
        return float(S.shape[1])
    return float((eig.sum() ** 2) / denom)


def bh_fdr(p_values: Sequence[float], *, alpha: float = 0.05) -> np.ndarray:
    """Benjamini-Hochberg FDR control. Returns a boolean rejection mask.

    Given an array of per-factor p-values, returns an array of the same
    length where ``True`` marks "reject the null at FDR ≤ alpha". Ties
    handled by the conservative (later-rank) tiebreak — i.e. ties never
    inflate the rejection count.
    """
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1:
        raise ValueError("p_values must be 1-D")
    n = p.shape[0]
    if n == 0:
        return np.zeros(0, dtype=bool)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    order = np.argsort(p, kind="mergesort")
    sorted_p = p[order]
    thresh = (np.arange(1, n + 1) / n) * alpha
    passed = sorted_p <= thresh
    if not passed.any():
        return np.zeros(n, dtype=bool)
    cutoff_rank = np.max(np.where(passed)[0])
    cutoff_p = sorted_p[cutoff_rank]
    return p <= cutoff_p
