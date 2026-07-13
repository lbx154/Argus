"""Disciplined autonomous model selection — nested walk-forward + successive halving.

This is the mechanism that lets Argus *choose* a model without fooling itself.
Given a development matrix (everything before the final quarantined test), it:

1. profiles the task and forms a task-conditional prior over the model space
   (:mod:`.task_profile`);
2. runs **nested walk-forward** (purged + embargoed folds, :mod:`..analysis.walk_forward`)
   — each candidate is trained on a fold's past and scored (per-day rank-IC) on the
   fold's future, so selection never touches the final OOS test;
3. prunes cheaply via **successive halving** (all candidates on fold 0, keep the
   top half, survivors get more folds) so a big space stays affordable;
4. records **every candidate×fold trial** to the hash-chained ledger before the
   winner is known (cherry-picking is visible);
5. picks by a **robust objective** (median fold rank-IC, not a lucky best fold) and
   reports the **effective number of trials** (:func:`..analysis.multiple_testing.effective_num_trials`)
   so the caller can deflate the final OOS Sharpe by the search's real breadth.

The final winner is retrained and OOS-tested by the caller (``model.backtest_predictions``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..analysis.walk_forward import WalkForwardConfig, WalkForwardValidator
from ..search_ledger import SearchLedger
from .registry import ModelSpec, default_model_space
from .task_profile import prior_for_profile, profile_task
from .trainers import build_trainer


def _daily_rank_ic(pred: np.ndarray, label: np.ndarray, days: np.ndarray) -> float:
    """Mean per-day cross-sectional rank-IC (Spearman) of pred vs label."""
    import pandas as pd

    df = pd.DataFrame({"pred": pred, "label": label, "day": days}).replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    if df.empty:
        return 0.0
    ics: list[float] = []
    for _d, g in df.groupby("day"):
        if len(g) < 10:
            continue
        ic = g["pred"].rank().corr(g["label"].rank())
        if pd.notna(ic):
            ics.append(float(ic))
    return float(np.mean(ics)) if ics else 0.0


@dataclass
class CandidateResult:
    spec: ModelSpec
    fold_rank_ics: list[float] = field(default_factory=list)

    @property
    def median_rank_ic(self) -> float:
        return float(np.median(self.fold_rank_ics)) if self.fold_rank_ics else 0.0

    @property
    def mean_rank_ic(self) -> float:
        return float(np.mean(self.fold_rank_ics)) if self.fold_rank_ics else 0.0

    @property
    def icir(self) -> float:
        if len(self.fold_rank_ics) < 2:
            return 0.0
        sd = float(np.std(self.fold_rank_ics, ddof=1))
        return float(np.mean(self.fold_rank_ics) / sd) if sd > 0 else 0.0


@dataclass
class SelectionResult:
    selected: ModelSpec
    ranked: list[CandidateResult]
    profile: dict[str, Any]
    prior: list[tuple[str, float, str]]
    effective_trials: float
    n_candidates: int
    n_ledger_rows: int


def _fold_windows(n_days: int, n_folds: int, *, purge: int, embargo: int):
    """Expanding purged/embargoed folds sized to yield ~``n_folds`` folds."""
    test_size = max(15, (n_days - purge - embargo) // (n_folds + 2))
    train_size = max(30, n_days - purge - embargo - (n_folds * test_size))
    cfg = WalkForwardConfig(
        train_size=train_size, test_size=test_size, step_size=test_size,
        window_type="expanding", purge_size=purge, embargo_size=embargo,
    )
    return cfg


def select_model(
    X: Any,
    y: Any,
    dev_mask: np.ndarray,
    *,
    ledger: SearchLedger,
    space: list[ModelSpec] | None = None,
    n_folds: int = 4,
    purge: int = 5,
    embargo: int = 2,
    halving: bool = True,
    run_id_prefix: str = "modelsel",
    universe: str = "",
    seed: int = 0,
) -> SelectionResult:
    """Autonomously select a model over ``dev_mask`` rows via nested walk-forward."""
    import pandas as pd

    space = space or default_model_space()
    profile = profile_task(X, y, dev_mask, seed=seed)
    prior = [(s.name, sc, r) for s, sc, r in prior_for_profile(profile, space)]
    prior_order = {name: i for i, (name, _, _) in enumerate(prior)}
    space_by_name = {s.name: s for s in space}

    # dev grid: numpy views once, fold masks by DATE (whole cross-section per day)
    dtidx = X.index.get_level_values("datetime")
    dev = np.asarray(dev_mask)
    dev_days = dtidx[dev].unique().sort_values()  # DatetimeIndex of dev days
    n_days = len(dev_days)
    Xnp = X.to_numpy()
    ynp = y.to_numpy().astype("float64")

    cfg = _fold_windows(n_days, n_folds, purge=purge, embargo=embargo)
    folds = list(WalkForwardValidator(cfg).split(n_days, labels=[str(d)[:10] for d in dev_days]))[:n_folds]
    if not folds:
        raise ValueError(f"no walk-forward folds from {n_days} dev days")

    results: dict[str, CandidateResult] = {s.name: CandidateResult(spec=s) for s in space}
    fold0_pred: dict[str, np.ndarray] = {}  # for effective-trials correlation
    n_rows = 0
    # successive halving: prior-ordered candidates, prune to top-half each fold
    active = sorted([s.name for s in space], key=lambda nm: prior_order.get(nm, 99))

    for fi, fold in enumerate(folds):
        train_dates = dev_days[fold.train_indices]
        test_dates = dev_days[fold.test_indices]
        k = max(1, int(len(fold.train_indices) * 0.15))
        valid_dates = dev_days[fold.train_indices][-k:]  # tail of train -> early-stop valid
        tr_mask = dev & np.asarray(dtidx.isin(train_dates))
        te_mask = dev & np.asarray(dtidx.isin(test_dates))
        va_day = np.asarray(dtidx.isin(valid_dates))
        fit_mask = tr_mask & ~va_day
        va_mask = tr_mask & va_day
        # drop NaN-label rows for fitting
        yfit_ok = fit_mask & np.isfinite(ynp)
        yva_ok = va_mask & np.isfinite(ynp)
        Xfit, yfit = Xnp[yfit_ok], ynp[yfit_ok]
        Xva, yva = Xnp[yva_ok], ynp[yva_ok]
        Xte = Xnp[te_mask]
        yte = ynp[te_mask]
        days_te = np.asarray(dtidx[te_mask])

        for name in active:
            spec = space_by_name[name]
            try:
                trainer = build_trainer(spec.family, {**spec.config, "seed": seed})
                trainer.fit(Xfit, yfit, Xva, yva)
                pred = trainer.predict(Xte)
                ic = _daily_rank_ic(pred, yte, days_te)
                status, err = "ok", ""
            except Exception as exc:  # noqa: BLE001 - a bad candidate must not sink the search
                pred, ic, status, err = np.zeros(len(Xte)), 0.0, "error", f"{type(exc).__name__}: {exc}"
            results[name].fold_rank_ics.append(ic)
            if fi == 0:
                fold0_pred[name] = pred
            ledger.append({
                "run_id": f"{run_id_prefix}-{name}-f{fold.fold_idx}",
                "factor_ids": [name],
                "weighting": "model_selection",
                "params": {"family": spec.family, "config_name": name, "config": spec.config,
                           "fold": fold.fold_idx, "fold_labels": fold.labels,
                           "prior_rank": prior_order.get(name)},
                "window": f"inner_fold_{fold.fold_idx}",
                "is_out_of_sample": False,
                "universe": universe,
                "status": status,
                "metrics": {"rank_ic": ic, "n_test_rows": int(len(Xte))},
                "error": err,
            })
            n_rows += 1

        if halving and len(active) > 1 and fi < len(folds) - 1:
            active = sorted(active, key=lambda nm: -results[nm].mean_rank_ic)[: max(1, (len(active) + 1) // 2)]

    # winner: among candidates that survived to the most folds, best median rank-IC
    max_folds = max(len(r.fold_rank_ics) for r in results.values())
    finalists = [r for r in results.values() if len(r.fold_rank_ics) == max_folds]
    selected = max(finalists, key=lambda r: r.median_rank_ic).spec

    # effective independent trials from fold-0 prediction correlations
    names0 = [n for n in fold0_pred]
    mat = np.column_stack([fold0_pred[n] for n in names0]) if len(names0) > 1 else np.zeros((1, 1))
    from ..analysis.multiple_testing import effective_num_trials

    eff = effective_num_trials(mat) if len(names0) > 1 else 1.0

    ranked = sorted(results.values(), key=lambda r: -r.median_rank_ic)
    return SelectionResult(
        selected=selected, ranked=ranked, profile=profile, prior=prior,
        effective_trials=round(float(eff), 3), n_candidates=len(space), n_ledger_rows=n_rows,
    )
