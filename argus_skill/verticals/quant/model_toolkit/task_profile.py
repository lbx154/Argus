"""Task-conditional model prior — "针对什么任务选什么模型".

:func:`profile_task` summarises a prediction task (size, feature families,
cross-section, a crude signal-to-noise proxy); :func:`prior_for_profile` turns
that profile into a *prior ordering* over the model space via transparent
heuristics (tabular + low SNR → trees/linear first; lots of data + many features
→ MLPs become competitive). The prior only orders which candidates to try first /
harder — the nested walk-forward evidence still decides the winner, so a wrong
prior costs compute, not correctness.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .registry import ModelSpec


def profile_task(X: Any, y: Any, train_mask: Any, *, sample: int = 20000, seed: int = 0) -> dict[str, Any]:
    """Summarise the (X, y) task over the training rows.

    Returns n_samples / n_features / fundamental vs technical counts / mean
    cross-section / a crude ``snr`` proxy (95th-pct |Pearson corr| of features
    vs label on a subsample). Cheap and side-effect free.
    """
    cols = list(X.columns)
    n_features = len(cols)
    n_fund = sum(1 for c in cols if str(c).startswith("fund_"))
    Xtr = X[train_mask]
    ytr = y[train_mask]
    keep = ytr.notna().to_numpy()
    n_samples = int(keep.sum())
    # mean cross-section (names per day) over train
    try:
        dt = Xtr.index.get_level_values("datetime")
        n_days = int(len(np.unique(dt)))
        cross_section = float(n_samples / max(1, n_days))
    except Exception:  # noqa: BLE001
        n_days, cross_section = 0, float(n_samples)

    # crude SNR: |corr(feature, label)| at the 95th percentile on a subsample
    rng = np.random.default_rng(seed)
    Xv = Xtr.to_numpy()[keep]
    yv = ytr.to_numpy()[keep].astype("float64")
    if len(yv) > sample:
        idx = rng.choice(len(yv), size=sample, replace=False)
        Xv, yv = Xv[idx], yv[idx]
    yv = yv - yv.mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        Xc = np.nan_to_num(Xv - np.nanmean(Xv, axis=0), nan=0.0)
        denom = np.sqrt((Xc ** 2).sum(0) * (yv ** 2).sum())
        corr = np.abs((Xc * yv[:, None]).sum(0) / np.where(denom > 0, denom, np.nan))
    snr = float(np.nanpercentile(corr, 95)) if np.isfinite(corr).any() else 0.0

    return {
        "n_samples": n_samples,
        "n_features": n_features,
        "n_fundamental": n_fund,
        "n_technical": n_features - n_fund,
        "n_days": n_days,
        "cross_section": round(cross_section, 1),
        "snr": round(snr, 4),
    }


def prior_for_profile(profile: dict[str, Any], space: list[ModelSpec]) -> list[tuple[ModelSpec, float, str]]:
    """Rank ``space`` by a task-conditional prior score (desc).

    Heuristics (transparent, not learned): trees are the tabular default and
    always score well; MLPs earn prior mass with more training rows and more
    features but are penalised on small data / very low SNR; a linear baseline is
    always kept. Returns ``(spec, score, reason)`` sorted high→low.
    """
    n = profile.get("n_samples", 0)
    nfeat = profile.get("n_features", 0)
    snr = profile.get("snr", 0.0)
    # data-adequacy for deep nets: rows per feature (a rough capacity signal)
    rows_per_feat = n / max(1, nfeat)
    data_rich = rows_per_feat > 300  # ~ enough rows to fit an MLP without wild overfit
    low_snr = snr < 0.01

    ranked: list[tuple[ModelSpec, float, str]] = []
    for spec in space:
        score, reason = 0.5, ""
        if spec.family == "gbdt":
            score = 0.9 - (0.1 if "high_capacity" in spec.tags else 0.0)
            reason = "trees are the strong tabular default"
        elif spec.family == "linear":
            score = 0.55 + (0.15 if low_snr else 0.0)
            reason = "cheap robust baseline" + (" (favoured at low SNR)" if low_snr else "")
        elif spec.family == "mlp":
            base = 0.4 + (0.25 if data_rich else -0.15) + min(0.15, nfeat / 4000.0)
            if "high_capacity" in spec.tags and not data_rich:
                base -= 0.2
            if low_snr:
                base -= 0.1
            score, reason = base, (
                f"MLP prior scaled by data ({'rich' if data_rich else 'thin'}, "
                f"{rows_per_feat:.0f} rows/feat)" + (" ; low SNR penalty" if low_snr else "")
            )
        ranked.append((spec, round(float(score), 3), reason))
    return sorted(ranked, key=lambda t: -t[1])
