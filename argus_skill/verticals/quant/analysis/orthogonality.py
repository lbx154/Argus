"""Orthogonality / incremental-value helpers for ``analysis.independence``.

A new factor's *incremental* signal over the established factor zoo is the
question the reviewer asks under ``analysis.independence``. Two cheap
diagnostics:

* :func:`correlation_matrix` — pairwise Pearson correlation of factor score
  series, NaN-tolerant (pairs dropped pairwise). The reviewer reads this to
  spot "two factors that are 0.95 correlated and we kept both".

* :func:`incremental_variance_share` — the fraction of a candidate factor's
  variance that is *not* explained by a linear combination of the existing
  factor set (the residual variance share after Gram-Schmidt). 0 means the
  candidate is fully redundant; 1 means fully orthogonal. A natural
  numerical answer to "is this just repackaged momentum?".
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def _aligned(series_map: Mapping[str, Sequence[float]]) -> tuple[tuple[str, ...], np.ndarray]:
    """Pack a name->series mapping into a 2-D array ``(T, K)``.

    All series must have equal length; that constraint is what "the search
    ledger keyed every trial by date" buys us. Raises ``ValueError`` on
    mismatch so the reviewer's report can quote a clean error if the inputs
    are malformed.
    """
    if not series_map:
        raise ValueError("series_map must be non-empty")
    keys = tuple(series_map.keys())
    lengths = {len(series_map[k]) for k in keys}
    if len(lengths) != 1:
        raise ValueError(
            f"series lengths differ: { {k: len(series_map[k]) for k in keys} }"
        )
    arr = np.column_stack([np.asarray(series_map[k], dtype=float) for k in keys])
    return keys, arr


def correlation_matrix(
    factor_series: Mapping[str, Sequence[float]],
) -> tuple[tuple[str, ...], np.ndarray]:
    """Pairwise Pearson correlation, NaN-pairwise-dropped.

    Returns ``(names, K x K matrix)`` where ``names[i]`` aligns with row /
    column ``i`` of the matrix. Diagonal is 1.0; pairs with fewer than 3
    common observations get NaN (visible to the reviewer as "we couldn't
    measure this — your trial count is too low").
    """
    names, mat = _aligned(factor_series)
    K = mat.shape[1]
    out = np.full((K, K), np.nan)
    for i in range(K):
        out[i, i] = 1.0
        for j in range(i + 1, K):
            mask = ~(np.isnan(mat[:, i]) | np.isnan(mat[:, j]))
            if mask.sum() < 3:
                continue
            xi = mat[mask, i]
            xj = mat[mask, j]
            if np.std(xi) == 0 or np.std(xj) == 0:
                continue
            out[i, j] = out[j, i] = float(np.corrcoef(xi, xj)[0, 1])
    return names, out


def incremental_variance_share(
    *,
    candidate: Sequence[float],
    existing: Mapping[str, Sequence[float]],
) -> float:
    """Variance share of ``candidate`` that survives Gram-Schmidt against ``existing``.

    Returns a number in [0, 1]:

    * ``1.0`` — candidate is fully orthogonal to the existing set.
    * ``0.0`` — candidate is a linear combination of the existing set.
    * Intermediate — fraction of variance that is genuinely new.

    Uses ordinary least squares (numpy.linalg.lstsq) on standardised inputs;
    NaN rows are dropped pairwise. Returns NaN if fewer than ``K + 2``
    common observations remain (under-determined).
    """
    cand = np.asarray(candidate, dtype=float)
    if not existing:
        # No reference set: everything is "new". Useful as a degenerate base.
        if cand.size == 0:
            return float("nan")
        finite = cand[~np.isnan(cand)]
        return 1.0 if finite.size > 0 else float("nan")
    names, X = _aligned(existing)
    if X.shape[0] != cand.shape[0]:
        raise ValueError(
            f"candidate length {cand.shape[0]} != existing length {X.shape[0]}"
        )
    mask = ~(np.isnan(cand) | np.any(np.isnan(X), axis=1))
    if mask.sum() < X.shape[1] + 2:
        return float("nan")
    y = cand[mask]
    A = X[mask]
    y_std = (y - y.mean()) / max(y.std(ddof=0), 1e-12)
    A_std = (A - A.mean(axis=0)) / np.maximum(A.std(axis=0, ddof=0), 1e-12)
    coef, *_ = np.linalg.lstsq(A_std, y_std, rcond=None)
    residual = y_std - A_std @ coef
    total_var = float(np.var(y_std, ddof=0))
    if total_var <= 0:
        return float("nan")
    return float(np.var(residual, ddof=0) / total_var)
