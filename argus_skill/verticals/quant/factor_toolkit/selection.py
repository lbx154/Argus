"""Feature-importance and redundancy selection — market-agnostic (sklearn).

Rank candidate factors by predictive contribution and flag collinear ones, so
the mining loop keeps a *diverse* factor set rather than many repackagings of
the same signal. Complements :mod:`..analysis.orthogonality` (which measures a
factor's incremental variance) with model-based importance.

* :func:`feature_importance_mdi` — mean-decrease-in-impurity from a random
  forest (fast, in-sample; biased toward high-cardinality features).
* :func:`feature_importance_permutation` — permutation importance on a
  held-out tail split (slower, out-of-sample, the more trustworthy ranking).
* :func:`identify_redundant_features` — from a correlation matrix, drop the
  lower-importance member of each highly-correlated pair.

Adapted from claude-trading-skills (MIT, © 2026 AGIPro):
feature-engineering/scripts/feature_importance.py.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def _clean_xy(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y).ravel()
    if X.ndim != 2:
        raise ValueError("X must be 2-D (n_samples, n_features)")
    if X.shape[0] != y.shape[0]:
        raise ValueError(f"X/y length mismatch: {X.shape[0]} vs {y.shape[0]}")
    keep = ~(np.isnan(X).any(axis=1) | np.isnan(y.astype(float)))
    return X[keep], y[keep]


def _names(feature_names: Sequence[str] | None, n: int) -> list[str]:
    if feature_names is None:
        return [f"f{i}" for i in range(n)]
    if len(feature_names) != n:
        raise ValueError(f"feature_names length {len(feature_names)} != {n} features")
    return list(feature_names)


def feature_importance_mdi(
    X: np.ndarray,
    y: np.ndarray,
    *,
    feature_names: Sequence[str] | None = None,
    n_estimators: int = 200,
    max_depth: int | None = 5,
    random_state: int = 42,
) -> dict[str, float]:
    """Random-forest mean-decrease-in-impurity importances, sorted descending."""
    from sklearn.ensemble import RandomForestClassifier  # lazy heavy import

    Xc, yc = _clean_xy(X, y)
    names = _names(feature_names, Xc.shape[1])
    model = RandomForestClassifier(
        n_estimators=n_estimators, max_depth=max_depth, random_state=random_state, n_jobs=-1
    )
    model.fit(Xc, yc)
    pairs = sorted(zip(names, model.feature_importances_), key=lambda kv: -kv[1])
    return {name: float(imp) for name, imp in pairs}


def feature_importance_permutation(
    X: np.ndarray,
    y: np.ndarray,
    *,
    feature_names: Sequence[str] | None = None,
    test_fraction: float = 0.3,
    n_estimators: int = 200,
    max_depth: int | None = 5,
    n_repeats: int = 10,
    random_state: int = 42,
) -> dict[str, float]:
    """Out-of-sample permutation importances on a time-ordered tail split.

    The last ``test_fraction`` of the (chronologically ordered) rows are held
    out — never shuffled — so the ranking respects time-series causality.
    """
    from sklearn.ensemble import RandomForestClassifier  # lazy heavy import
    from sklearn.inspection import permutation_importance

    Xc, yc = _clean_xy(X, y)
    names = _names(feature_names, Xc.shape[1])
    split = int(round(Xc.shape[0] * (1.0 - test_fraction)))
    if split < 1 or split >= Xc.shape[0]:
        raise ValueError("test_fraction leaves an empty train or test split")
    model = RandomForestClassifier(
        n_estimators=n_estimators, max_depth=max_depth, random_state=random_state, n_jobs=-1
    )
    model.fit(Xc[:split], yc[:split])
    result = permutation_importance(
        model, Xc[split:], yc[split:], n_repeats=n_repeats, random_state=random_state
    )
    pairs = sorted(zip(names, result.importances_mean), key=lambda kv: -kv[1])
    return {name: float(imp) for name, imp in pairs}


def identify_redundant_features(
    importances: Mapping[str, float],
    names: Sequence[str],
    corr_matrix: np.ndarray,
    *,
    threshold: float = 0.9,
) -> list[str]:
    """Return the features to DROP as redundant.

    For every pair whose absolute correlation exceeds ``threshold``, the member
    with the lower importance is marked for removal. ``names`` aligns with the
    rows/cols of ``corr_matrix`` (e.g. the output of
    :func:`..analysis.orthogonality.correlation_matrix`).
    """
    corr = np.asarray(corr_matrix, dtype=float)
    k = len(names)
    if corr.shape != (k, k):
        raise ValueError(f"corr_matrix {corr.shape} does not match {k} names")
    drop: set[str] = set()
    for i in range(k):
        for j in range(i + 1, k):
            c = corr[i, j]
            if np.isnan(c) or abs(c) <= threshold:
                continue
            a, b = names[i], names[j]
            weaker = a if importances.get(a, 0.0) < importances.get(b, 0.0) else b
            drop.add(weaker)
    return [n for n in names if n in drop]
