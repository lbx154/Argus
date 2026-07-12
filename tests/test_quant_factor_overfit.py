"""Tests for IC-based factor-overfit diagnostics (analysis.factor_overfit)."""
from __future__ import annotations

import numpy as np

from argus_skill.verticals.quant.analysis import factor_overfit as fo


def _predictive(seed: int, T: int = 300, S: int = 60, strength: float = 0.05, phi: float = 0.9):
    """Return (factor, one_period_returns) where factor[t] predicts return t->t+1.

    The factor is a persistent AR(1) so its predictive power decays slowly across
    forward horizons (a realistic, non-trivial IC half-life).
    """
    rng = np.random.default_rng(seed)
    factor = np.zeros((T, S))
    factor[0] = rng.normal(0, 1, S)
    innov = np.sqrt(1.0 - phi**2)
    for t in range(1, T):
        factor[t] = phi * factor[t - 1] + rng.normal(0, innov, S)
    ret = rng.normal(0, 0.02, (T, S))
    ret[1:] += strength * factor[:-1]  # return[t] driven by factor[t-1]
    return factor, ret


def test_cross_sectional_ic_shape_and_sign():
    factor, ret = _predictive(0)
    fwd = fo._forward_cum_return(ret, 1)
    ic = fo.cross_sectional_ic(factor, fwd)
    assert ic.shape == (factor.shape[0],)
    assert np.nanmean(ic) > 0.1  # genuinely predictive


def test_ic_stability_pass_and_fail():
    factor, ret = _predictive(1)
    fwd = fo._forward_cum_return(ret, 1)
    ic_real = fo.cross_sectional_ic(factor, fwd)
    assert fo.ic_stability(ic_real).passed
    # pure noise IC (centered at 0) fails
    ic_noise = np.random.default_rng(2).normal(0, 0.05, 300)
    assert not fo.ic_stability(ic_noise).passed


def test_placebo_separates_real_from_noise():
    factor, ret = _predictive(3)
    fwd = fo._forward_cum_return(ret, 1)
    real = fo.placebo_test(factor, fwd, n_permutations=30)
    assert real.passed and real.details["p_value"] < 0.05
    noise = np.random.default_rng(4).normal(0, 1, factor.shape)
    assert not fo.placebo_test(noise, fwd, n_permutations=30).passed


def test_ic_half_life_positive_for_persistent_signal():
    factor, ret = _predictive(5)
    res = fo.ic_half_life(factor, ret, horizons=(1, 2, 5, 10, 20))
    assert res.passed
    assert res.details["half_life_periods"] is not None


def test_market_regime_labels_and_subsample():
    factor, ret = _predictive(6)
    fwd = fo._forward_cum_return(ret, 1)
    ic = fo.cross_sectional_ic(factor, fwd)
    labels = fo.market_regime_labels(ret)
    assert labels.shape == (factor.shape[0],)
    assert fo.subsample_stress(ic, labels).passed


def test_report_real_beats_noise():
    factor, ret = _predictive(7)
    noise = np.random.default_rng(8).normal(0, 1, factor.shape)
    real = fo.factor_overfit_report(factor, ret, horizons=(1, 2, 5, 10, 20))
    noise_rep = fo.factor_overfit_report(noise, ret, horizons=(1, 2, 5, 10, 20))
    assert real["score"] == 4
    assert real["score"] > noise_rep["score"]
