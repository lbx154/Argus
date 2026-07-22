"""Deterministic tests for the autonomous model-selection toolkit (no network/dump).

Covers the config->model trainers (gbdt/mlp/linear on numpy), the task profiler +
prior, effective-number-of-trials, and the nested walk-forward selector (every
candidate x fold trial ledgered, robust winner, hash-chain intact). Uses tiny
synthetic data so it runs in seconds; real lightgbm/torch/sklearn are exercised.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from argus_skill.verticals.quant.analysis.multiple_testing import effective_num_trials
from argus_skill.verticals.quant.model_toolkit import (
    available_families,
    build_trainer,
    default_model_space,
    prior_for_profile,
    profile_task,
    select_model,
)
from argus_skill.verticals.quant.model_toolkit.registry import ModelSpec
from argus_skill.verticals.quant.search_ledger import SearchLedger


def _panel(seed=0, n_days=150, n_codes=30, n_feat=4):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_days, freq="B")
    codes = [f"C{i:02d}" for i in range(n_codes)]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "instrument"])
    f = rng.normal(size=(len(idx), n_feat)).astype("float32")
    y = 3.0 * f[:, 0] - 2.0 * f[:, 1] + 0.5 * rng.normal(size=len(idx))
    X = pd.DataFrame({f"a360_f{i}": f[:, i] for i in range(n_feat)}, index=idx)
    return X, pd.Series(y, index=idx, name="label"), dates


def test_families_and_trainers_learn():
    assert set(available_families()) == {"gbdt", "mlp", "linear"}
    X, y, _ = _panel()
    Xtr, ytr = X.to_numpy()[:3000], y.to_numpy()[:3000]
    Xte, yte = X.to_numpy()[3000:], y.to_numpy()[3000:]
    for fam, cfg in [("gbdt", {"num_boost_round": 100}),
                     ("linear", {"alpha": 1.0}),
                     ("mlp", {"hidden_dims": (32,), "epochs": 30, "batch_size": 512})]:
        t = build_trainer(fam, {**cfg, "seed": 0}).fit(Xtr, ytr, Xte, yte)
        pred = t.predict(Xte)
        assert np.corrcoef(pred, yte)[0, 1] > 0.5, f"{fam} failed to learn"


def test_task_profile_and_prior():
    X, y, dates = _panel()
    dev = np.asarray(X.index.get_level_values("datetime") <= dates[119])
    prof = profile_task(X, y, dev)
    assert prof["n_features"] == 4 and prof["n_samples"] > 0 and prof["snr"] > 0
    ranked = prior_for_profile(prof, default_model_space())
    # trees lead the prior on a small tabular task
    assert ranked[0][0].family == "gbdt"
    # every candidate is scored with a stated reason
    assert all(r for (_s, _sc, r) in ranked)


def test_effective_num_trials_bounds():
    rng = np.random.default_rng(1)
    base = rng.normal(size=(500, 1))
    identical = np.hstack([base, base, base])          # 3 identical -> ~1 effective
    orthogonal = rng.normal(size=(500, 3))             # ~independent -> ~3 effective
    assert effective_num_trials(identical) < 1.2
    assert effective_num_trials(orthogonal) > 2.3


def test_select_model_ledgers_every_trial_and_picks_positive():
    X, y, dates = _panel()
    dev = np.asarray(X.index.get_level_values("datetime") <= dates[119])
    space = [s for s in default_model_space() if s.name in ("gbdt_shallow", "ridge", "mlp_small")]
    # trim MLP epochs for test speed
    space = [ModelSpec(s.name, s.family, {**s.config, **({"epochs": 25} if s.family == "mlp" else {})},
                       s.description, s.tags) for s in space]
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ledger = SearchLedger(os.path.join(d, "model_ledger.jsonl"))
        res = select_model(X, y, dev, ledger=ledger, space=space, n_folds=3, seed=0)

        # successive halving over 3 folds with 3 candidates: 3 + 2 + 1 = 6 trials
        assert res.n_ledger_rows == 6
        rows = ledger.rows()
        assert len(rows) == 6
        assert ledger.verify_chain()
        # every ledger row is a model-selection trial with a rank_ic metric
        for row in rows:
            assert row.payload["weighting"] == "model_selection"
            assert "rank_ic" in row.payload["metrics"]

    # the winner learned a real signal and is one of the three
    assert res.selected.name in ("gbdt_shallow", "ridge", "mlp_small")
    best = max(res.ranked, key=lambda r: r.median_rank_ic)
    assert best.median_rank_ic > 0.1
    assert 1.0 <= res.effective_trials <= 3.0
