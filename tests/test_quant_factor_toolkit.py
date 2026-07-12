"""Tests for the factor-construction toolkit (factor_toolkit)."""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from argus_skill.verticals.quant import factor_toolkit as ft
from argus_skill.verticals.quant.backtest import BacktestSpec
from argus_skill.verticals.quant.executor import ForcingExecutor
from argus_skill.verticals.quant.reference_engine import ToyBacktestEngine
from argus_skill.verticals.quant.search_ledger import SearchLedger

# ── price features ──────────────────────────────────────────────────

def test_momentum_shape_and_no_lookahead():
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, (60, 10)), axis=0)
    mom = ft.price_features.momentum(close, 20)
    assert mom.shape == close.shape
    assert np.isnan(mom[:20]).all()  # warm-up is NaN
    assert not np.isnan(mom[20:]).any()  # everything after is defined


def test_reversal_is_negated_momentum():
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, (40, 5)), axis=0)
    np.testing.assert_allclose(
        ft.price_features.reversal(close, 5),
        -ft.price_features.momentum(close, 5),
        equal_nan=True,
    )


def test_close_position_bounds():
    high = np.array([[10.0], [10.0]])
    low = np.array([[8.0], [8.0]])
    close = np.array([[9.0], [8.0]])
    pos = ft.price_features.close_position(high, low, close)
    assert np.isclose(pos[0, 0], 0.5)  # mid-range
    assert np.isclose(pos[1, 0], 0.0)  # at the low


# ── statistical diagnostics ─────────────────────────────────────────

def test_hurst_monotonic_trend_vs_antipersistent():
    rng = np.random.default_rng(2)
    e = rng.normal(0, 1, 2000)
    trend = np.cumsum(0.05 + e)  # persistent
    anti = e[1:] - e[:-1]  # MA(1) negative -> anti-persistent
    h_trend = ft.statistical.hurst_exponent(trend)
    h_anti = ft.statistical.hurst_exponent(anti)
    assert h_anti < 0.5 < h_trend


def test_hurst_too_short_raises():
    with pytest.raises(ValueError):
        ft.statistical.hurst_exponent(np.arange(5), min_window=10)


def test_half_life_and_adf_on_mean_reverting_ar1():
    rng = np.random.default_rng(3)
    x = np.zeros(600)
    for t in range(1, 600):
        x[t] = 0.9 * x[t - 1] + rng.normal(0, 1)  # slow reversion toward 0
    hl = ft.statistical.half_life(x)
    adf = ft.statistical.adf_test(x)
    assert hl["half_life"] > 0 and hl["beta"] < 0
    assert adf["test_statistic"] < -2.86  # rejects unit root at 5%


def test_half_life_not_mean_reverting_returns_negative_flag():
    walk = np.cumsum(np.ones(200))  # pure trend, beta >= 0
    assert ft.statistical.half_life(walk)["half_life"] == -1.0


def test_variance_ratio_near_one_for_random_walk():
    rng = np.random.default_rng(4)
    rw = np.cumsum(rng.normal(0, 1, 2000)) + 500
    vr = ft.statistical.variance_ratio(rw, q=4)
    assert abs(vr["vr"] - 1.0) < 0.25


# ── volatility ──────────────────────────────────────────────────────

def test_realized_vol_shape_and_positive():
    rng = np.random.default_rng(5)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, (100, 8)), axis=0)
    vol = ft.volatility.realized_vol(close, window=20)
    assert vol.shape == close.shape
    tail = vol[20:]
    assert np.all(tail[~np.isnan(tail)] >= 0)


def test_vol_1d_input_returns_1d():
    rng = np.random.default_rng(6)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, 100))
    assert ft.volatility.realized_vol(close, window=10).shape == (100,)


# ── regime ──────────────────────────────────────────────────────────

def test_atr_adx_bbwidth_shapes():
    rng = np.random.default_rng(7)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, 200))
    high, low = close * 1.01, close * 0.99
    assert ft.regime.atr(high, low, close).shape == (200,)
    assert ft.regime.adx(high, low, close).shape == (200,)
    assert ft.regime.bb_width(close).shape == (200,)


def test_classify_regime_labels():
    r = ft.regime.classify_regime(
        vol_percentile=0.1, adx_value=30, hurst_value=0.3, trend_direction=1
    )
    assert r["volatility"] == "low"
    assert r["trend"] == "trending"
    assert r["direction"] == "up"
    assert r["mean_reversion"] == "mean_reverting"
    assert r["quadrant"] == "low_vol_trending"


# ── selection (sklearn) ─────────────────────────────────────────────

def test_feature_importance_ranks_informative_feature_first():
    rng = np.random.default_rng(8)
    n = 800
    informative = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, (n, 3))
    y = (informative > 0).astype(int)
    X = np.column_stack([informative, noise])
    names = ["signal", "n1", "n2", "n3"]
    imp = ft.selection.feature_importance_mdi(X, y, feature_names=names, n_estimators=100)
    assert max(imp, key=imp.get) == "signal"


def test_identify_redundant_features_drops_weaker_of_correlated_pair():
    names = ["a", "b", "c"]
    corr = np.array([[1.0, 0.95, 0.1], [0.95, 1.0, 0.1], [0.1, 0.1, 1.0]])
    importances = {"a": 0.5, "b": 0.2, "c": 0.3}
    dropped = ft.selection.identify_redundant_features(importances, names, corr, threshold=0.9)
    assert dropped == ["b"]  # b is the weaker of the a~b correlated pair


# ── the bridge (end-to-end through the existing engine) ─────────────

def test_build_feature_panel_runs_through_toy_engine_and_ledger():
    rng = np.random.default_rng(9)
    T, S = 120, 25
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, (T, S)), axis=0)
    ohlcv = {"close": close, "high": close * 1.01, "low": close * 0.99, "open": close}
    fwd = rng.normal(0.001, 0.02, (T, S))

    panel, registry = ft.build_feature_panel(ohlcv, fwd)
    assert panel.factor_values.shape[2] == len(registry.factor_ids())
    assert set(panel.factor_order) == set(registry.factor_ids())
    # every FactorSpec carries a non-empty economic description
    for fid in registry.factor_ids():
        assert registry.get(fid).description.strip()

    engine = ToyBacktestEngine(panel=panel, registry=registry)
    ledger = SearchLedger(os.path.join(tempfile.mkdtemp(), "SEARCH_LEDGER.jsonl"))
    ex = ForcingExecutor(engine=engine, ledger=ledger)
    for fid in registry.factor_ids():
        res, _ = ex.submit(
            BacktestSpec(run_id=f"t-{fid}", factor_ids=[fid], weighting="single", window="test")
        )
        assert res.status == "ok" and "ic" in res.metrics
    assert len(ledger) == len(registry.factor_ids())
    assert ledger.verify_chain()


def test_build_feature_panel_validates_shapes():
    close = np.ones((10, 3))
    with pytest.raises(ValueError):
        ft.build_feature_panel({"close": close}, np.ones((10, 4)))  # fwd shape mismatch
    with pytest.raises(ValueError):
        ft.build_feature_panel({"high": close}, np.ones((10, 3)))  # missing 'close'


def test_default_catalog_has_diverse_directions():
    catalog = ft.default_feature_catalog()
    names = {f.name for f in catalog}
    assert "momentum_20" in names and "reversal_5" in names
    # low-vol factor encodes the anomaly with a negative direction
    low_vol = next(f for f in catalog if f.name.startswith("low_vol"))
    assert low_vol.direction == -1.0
