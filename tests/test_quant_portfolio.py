"""Deterministic tests for portfolio construction (numpy-only, no network)."""
from __future__ import annotations

import numpy as np
import pytest

from argus_skill.verticals.quant.portfolio import book_returns, sharpe_maxdd, to_weights


def test_weights_dollar_neutral_gross_one_and_capped():
    rng = np.random.default_rng(0)
    s = rng.normal(size=50)
    w = to_weights(s, max_weight=0.05)
    assert abs(w.sum()) < 1e-9              # dollar-neutral
    assert abs(np.abs(w).sum() - 1.0) < 1e-9  # gross = 1
    assert np.abs(w).max() <= 0.05 + 1e-9   # cap respected
    # monotone: highest score gets the most positive weight
    assert np.argmax(w) == np.argmax(s)


def test_cap_is_preserved_after_full_investment():
    w = to_weights(np.arange(6.0), max_weight=0.18)
    assert abs(w.sum()) < 1e-9
    assert abs(np.abs(w).sum() - 1.0) < 1e-9
    assert np.abs(w).max() <= 0.18 + 1e-9


def test_infeasible_cap_raises_instead_of_silently_breaking_it():
    with pytest.raises(ValueError, match="infeasible"):
        to_weights(np.arange(5.0), max_weight=0.1)


def test_nan_scores_get_zero_weight():
    s = np.array([1.0, np.nan, -1.0, 2.0, np.nan])
    w = to_weights(s, max_weight=1.0)
    assert w[1] == 0.0 and w[4] == 0.0
    assert abs(w.sum()) < 1e-9


def test_size_neutralization_removes_size_loading():
    rng = np.random.default_rng(1)
    size = rng.normal(size=200)
    # score deliberately correlated with size
    score = 0.8 * size + 0.2 * rng.normal(size=200)
    w_raw = to_weights(score, max_weight=1.0)
    w_neu = to_weights(score, size=size, neutralize_size=True, max_weight=1.0)
    # after neutralisation the book's size exposure (w·size) is ~0 and far smaller
    assert abs(float(w_neu @ size)) < abs(float(w_raw @ size))
    assert abs(float(w_neu @ size)) < 1e-6


def test_inv_vol_downweights_volatile_names():
    # two names with identical (opposite) signal but different vol
    s = np.array([0.5, -0.5, 0.4, -0.4])
    vol = np.array([1.0, 1.0, 4.0, 4.0])  # names 2,3 far more volatile
    w = to_weights(s, vol=vol, inv_vol=True, max_weight=1.0)
    assert abs(w[0]) > abs(w[2])          # low-vol long bigger than high-vol long
    assert abs(w[1]) > abs(w[3])


def test_book_returns_and_sharpe():
    w = [np.array([0.5, -0.5]), np.array([0.5, -0.5])]
    r = [np.array([0.02, -0.01]), np.array([0.01, 0.0])]
    net = book_returns(w, r, cost=0.0)
    assert net[0] == 0.5 * 0.02 - 0.5 * (-0.01)   # 0.015
    assert net[1] == 0.5 * 0.01                     # no turnover (same book), 0.005
    sh, dd = sharpe_maxdd(net, periods_per_year=12.0)
    assert sh > 0 and dd <= 0.0


def test_book_returns_rejects_misaligned_periods_and_shapes():
    with pytest.raises(ValueError, match="same number"):
        book_returns(
            [np.array([1.0]), np.array([1.0])],
            [np.array([0.1])],
            cost=0.0,
        )
    with pytest.raises(ValueError, match="differ at rebalance"):
        book_returns([np.array([0.5, -0.5])], [np.array([0.1])], cost=0.0)
