"""Backtest-overfitting diagnostics — market-agnostic multiple-testing tools.

Complements :mod:`.multiple_testing` (which already ships the Deflated Sharpe
Ratio, the Benjamini-Hochberg FDR mask, and the Bonferroni-style Sharpe
haircut). This module adds the search-process diagnostics that read a *set* of
strategy/return series rather than a single headline number:

* :func:`probability_of_backtest_overfitting` — PBO via combinatorial purged
  cross-validation (Bailey, Borwein, López de Prado & Zhu, 2017): how often does
  the in-sample-best strategy underperform out-of-sample? PBO > 0.5 means the
  selection process is more likely than not producing an overfit pick.
* :func:`minimum_backtest_length` — the shortest record over which a target
  Sharpe is distinguishable from zero at a confidence level.
* :func:`bonferroni_mask` / :func:`holm_mask` — family-wise error corrections
  over a vector of per-strategy p-values (the FWER counterparts to the existing
  FDR ``bh_fdr``).

The Deflated Sharpe Ratio is NOT re-implemented here — import it from
:func:`argus_skill.verticals.quant.analysis.multiple_testing.deflated_sharpe_ratio`.

Pure numpy + scipy; no market, calendar, or cost assumptions.

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
walk-forward-validation/scripts/overfit_detector.py.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class PBOResult:
    """Probability-of-backtest-overfitting outcome.

    ``pbo`` is the headline (fraction of CPCV paths on which the in-sample-best
    strategy landed in the worse OOS half). ``logit_values`` are the per-path
    logits of the OOS relative rank, kept so the caller can inspect the
    distribution rather than only the point estimate.
    """

    pbo: float
    n_paths: int
    n_overfit_paths: int
    mean_oos_rank: float
    is_overfit: bool
    logit_values: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class MinBTLResult:
    """Minimum backtest length for a target (per-period) Sharpe ratio."""

    min_length: int
    target_sharpe: float
    confidence: float
    skewness: float
    kurtosis: float


def probability_of_backtest_overfitting(
    strategy_returns: np.ndarray,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
) -> PBOResult:
    """Estimate PBO by combinatorial purged cross-validation.

    Parameters
    ----------
    strategy_returns
        2-D array ``(n_observations, n_strategies)``; each column is one
        strategy's return series over a common time axis.
    n_groups
        Number of contiguous time groups the observations are split into.
    n_test_groups
        Groups held out as the OOS test set in each C(n_groups, n_test_groups)
        combination; the rest are in-sample.

    For each split it picks the strategy with the best in-sample mean return,
    then measures that strategy's relative rank out-of-sample. PBO is the share
    of splits where the IS-best strategy fell into the worse OOS half.

    Raises ``ValueError`` for fewer than 2 strategies, fewer than 3 groups, or
    groups too small (< 5 observations each).
    """
    arr = np.asarray(strategy_returns, dtype=float)
    if arr.ndim != 2:
        raise ValueError("strategy_returns must be a 2-D (n_obs, n_strategies) array")
    n_obs, n_strategies = arr.shape
    if n_strategies < 2:
        raise ValueError("need at least 2 strategies for PBO")
    if n_groups < 3:
        raise ValueError("need at least 3 groups for meaningful CPCV")

    group_size = n_obs // n_groups
    if group_size < 5:
        raise ValueError(
            f"each group has only {group_size} observations; "
            "reduce n_groups or provide more data"
        )

    bounds = [
        (i * group_size, (i + 1) * group_size if i < n_groups - 1 else n_obs)
        for i in range(n_groups)
    ]

    logit_values: list[float] = []
    n_overfit = 0
    for test_combo in combinations(range(n_groups), n_test_groups):
        test_set = set(test_combo)
        train_idx: list[int] = []
        test_idx: list[int] = []
        for g, (start, end) in enumerate(bounds):
            (test_idx if g in test_set else train_idx).extend(range(start, end))
        if not train_idx or not test_idx:
            continue

        is_perf = np.mean(arr[np.asarray(train_idx)], axis=0)
        oos_perf = np.mean(arr[np.asarray(test_idx)], axis=0)
        is_best = int(np.argmax(is_perf))

        # Relative OOS rank of the IS-best strategy, 1-based & normalised.
        worse_or_equal = int(np.sum(oos_perf > oos_perf[is_best]))
        relative_rank = (worse_or_equal + 1) / n_strategies
        clamped = float(np.clip(relative_rank, 0.01, 0.99))
        logit_values.append(float(np.log(clamped / (1.0 - clamped))))
        if relative_rank > 0.5:
            n_overfit += 1

    n_paths = len(logit_values)
    pbo = n_overfit / n_paths if n_paths else 1.0
    mean_rank = (
        float(np.mean([1.0 / (1.0 + np.exp(-lv)) for lv in logit_values]))
        if logit_values
        else 1.0
    )
    return PBOResult(
        pbo=pbo,
        n_paths=n_paths,
        n_overfit_paths=n_overfit,
        mean_oos_rank=mean_rank,
        is_overfit=pbo > 0.5,
        logit_values=logit_values,
    )


def minimum_backtest_length(
    *,
    target_sharpe: float,
    confidence: float = 0.95,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> MinBTLResult:
    """Minimum number of observations to call a per-period Sharpe non-zero.

    ``target_sharpe`` is the NON-annualised (per-observation) Sharpe you want to
    distinguish from zero at ``confidence``. ``kurtosis`` is raw (3.0 = normal).
    Larger skew/kurtosis inflate the requirement. Raises ``ValueError`` unless
    ``target_sharpe > 0`` and ``0 < confidence < 1``.
    """
    if target_sharpe <= 0:
        raise ValueError("target_sharpe must be > 0")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    z = float(norm.ppf(confidence))
    variance_factor = (
        1.0 - skewness * target_sharpe + (kurtosis - 1.0) / 4.0 * target_sharpe**2
    )
    min_length = int(np.ceil(1.0 + variance_factor * (z / target_sharpe) ** 2))
    return MinBTLResult(
        min_length=min_length,
        target_sharpe=target_sharpe,
        confidence=confidence,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def bonferroni_mask(p_values: Sequence[float], *, alpha: float = 0.05) -> np.ndarray:
    """Bonferroni family-wise correction → boolean rejection mask.

    ``True`` marks a strategy whose p-value clears the ``alpha / N`` threshold.
    The FWER counterpart to the FDR ``bh_fdr``; more conservative.
    """
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1:
        raise ValueError("p_values must be 1-D")
    n = p.shape[0]
    if n == 0:
        return np.zeros(0, dtype=bool)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    return p < (alpha / n)


def holm_mask(p_values: Sequence[float], *, alpha: float = 0.05) -> np.ndarray:
    """Holm-Bonferroni step-down correction → boolean rejection mask.

    Sorts p-values ascending and rejects while ``p_(k) <= alpha / (N - k)``,
    stopping at the first failure. Uniformly more powerful than plain
    Bonferroni while controlling the same family-wise error rate.
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
    mask = np.zeros(n, dtype=bool)
    for rank, idx in enumerate(order):
        if p[idx] <= alpha / (n - rank):
            mask[idx] = True
        else:
            break  # step-down: stop at the first non-rejection
    return mask
