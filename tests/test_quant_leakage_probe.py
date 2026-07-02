"""The NaN-future leakage probe must FAIL a look-ahead engine (R3-3).

The earlier probe compared IC before/after masking forward_returns, but IC IS
Spearman(score, forward_returns) — masking nulls it for ANY engine, so the probe
passed unconditionally (a perfect look-ahead engine passed too). The fix watches
a score-derived, forward-independent metric (turnover) for INVARIANCE instead.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from argus_skill.verticals.quant.backtest import BacktestSpec
from argus_skill.verticals.quant.factors import FactorSpec, InMemoryFactorRegistry
from argus_skill.verticals.quant.leakage_probe import NaNFutureLeakageProbe
from argus_skill.verticals.quant.reference_engine import (
    ToyBacktestEngine,
    ToyPanel,
    make_synthetic_panel,
)


def _fixture():
    fspec = FactorSpec(factor_id="f0", source="toy", direction=1.0)
    registry = InMemoryFactorRegistry.from_iter([fspec])
    panel = make_synthetic_panel(factor_specs=(fspec,))
    spec = BacktestSpec(run_id="r1", factor_ids=["f0"], weighting="single")
    return panel, registry, spec


class _LeakyEngine(ToyBacktestEngine):
    """Perfect look-ahead: its score IS the forward returns (reads the future)."""

    def _combine(self, sub, signs, weighting):  # noqa: ARG002 — leak ignores factors
        return np.asarray(self.panel.forward_returns, dtype=float)


def test_clean_engine_passes_leakage_probe():
    panel, registry, spec = _fixture()
    engine = ToyBacktestEngine(panel=panel, registry=registry)
    report = NaNFutureLeakageProbe().check(engine, spec)
    assert report.passed is True


def test_leaky_engine_fails_leakage_probe():
    # The whole point: a score that reads forward_returns MUST be caught. The old
    # IC-based probe passed this engine (IC collapsed to NaN -> "no look-ahead").
    panel, registry, spec = _fixture()
    engine = _LeakyEngine(panel=panel, registry=registry)
    report = NaNFutureLeakageProbe().check(engine, spec)
    assert report.passed is False


def test_probe_restores_forward_returns():
    panel, registry, spec = _fixture()
    before = np.asarray(panel.forward_returns).copy()
    NaNFutureLeakageProbe().check(ToyBacktestEngine(panel=panel, registry=registry), spec)
    assert np.allclose(panel.forward_returns, before, equal_nan=True)


def test_equal_weight_keeps_mean_over_finite_factor_ranks():
    factors = (
        FactorSpec(factor_id="f0", source="toy", direction=1.0),
        FactorSpec(factor_id="f1", source="toy", direction=1.0),
    )
    registry = InMemoryFactorRegistry.from_iter(factors)
    panel = ToyPanel(
        factor_values=np.array(
            [[[1.0, np.nan], [2.0, np.nan], [3.0, 30.0], [4.0, 10.0]]]
        ),
        forward_returns=np.zeros((1, 4), dtype=float),
        factor_order=("f0", "f1"),
    )
    engine = ToyBacktestEngine(panel=panel, registry=registry)
    sub, signs = engine._resolve_factor_columns(("f0", "f1"))

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        score = engine._combine(sub, signs, "equal_weight")

    expected = np.array([[0.0, 1.0 / 3.0, 5.0 / 6.0, 0.5]])
    assert np.allclose(score, expected)


def test_equal_weight_all_nan_factor_ranks_do_not_warn():
    factors = (
        FactorSpec(factor_id="f0", source="toy", direction=1.0),
        FactorSpec(factor_id="f1", source="toy", direction=1.0),
    )
    registry = InMemoryFactorRegistry.from_iter(factors)
    panel = ToyPanel(
        factor_values=np.full((3, 4, 2), np.nan, dtype=float),
        forward_returns=np.arange(12, dtype=float).reshape(3, 4) / 100.0,
        factor_order=("f0", "f1"),
    )
    engine = ToyBacktestEngine(panel=panel, registry=registry)
    equal_sub, equal_signs = engine._resolve_factor_columns(("f0", "f1"))
    single_sub, single_signs = engine._resolve_factor_columns(("f0",))

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        equal_score = engine._combine(equal_sub, equal_signs, "equal_weight")
        single_score = engine._combine(single_sub, single_signs, "single")

    assert np.isnan(equal_score).all()
    assert np.isnan(single_score).all()


@pytest.mark.parametrize("weighting", ["single", "equal_weight"])
def test_all_nan_run_ic_aggregation_does_not_warn(weighting):
    if weighting == "single":
        factors = (FactorSpec(factor_id="f0", source="toy", direction=1.0),)
        factor_order = ("f0",)
        factor_ids = ["f0"]
        factor_values = np.full((3, 4, 1), np.nan, dtype=float)
    else:
        factors = (
            FactorSpec(factor_id="f0", source="toy", direction=1.0),
            FactorSpec(factor_id="f1", source="toy", direction=1.0),
        )
        factor_order = ("f0", "f1")
        factor_ids = ["f0", "f1"]
        factor_values = np.full((3, 4, 2), np.nan, dtype=float)
    registry = InMemoryFactorRegistry.from_iter(factors)
    panel = ToyPanel(
        factor_values=factor_values,
        forward_returns=np.arange(12, dtype=float).reshape(3, 4) / 100.0,
        factor_order=factor_order,
    )
    spec = BacktestSpec(
        run_id=f"all-nan-run-{weighting}",
        factor_ids=factor_ids,
        weighting=weighting,
    )
    engine = ToyBacktestEngine(panel=panel, registry=registry)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = engine.run(spec)

    assert np.isnan(result.metrics["ic"])
    assert np.isnan(result.metrics["icir"])


def test_mixed_nan_run_ic_aggregation_matches_finite_values():
    fspec = FactorSpec(factor_id="f0", source="toy", direction=1.0)
    registry = InMemoryFactorRegistry.from_iter([fspec])
    panel = ToyPanel(
        factor_values=np.array(
            [
                [[np.nan], [np.nan], [np.nan], [np.nan]],
                [[1.0], [2.0], [3.0], [4.0]],
                [[1.0], [2.0], [3.0], [4.0]],
            ],
            dtype=float,
        ),
        forward_returns=np.array(
            [
                [0.04, 0.03, 0.02, 0.01],
                [0.01, 0.02, 0.03, 0.04],
                [0.04, 0.03, 0.02, 0.01],
            ],
            dtype=float,
        ),
        factor_order=("f0",),
    )
    spec = BacktestSpec(
        run_id="mixed-nan-run",
        factor_ids=["f0"],
        weighting="single",
    )
    engine = ToyBacktestEngine(panel=panel, registry=registry)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = engine.run(spec)

    assert np.isclose(result.metrics["ic"], 0.0)
    assert np.isclose(result.metrics["icir"], 0.0)


def test_missing_panel_is_a_failing_noop():
    _panel, _registry, spec = _fixture()

    class _NoPanel:
        def run(self, spec):  # noqa: ARG002
            from argus_skill.verticals.quant.backtest import BacktestResult
            return BacktestResult(run_id="r", metrics={"turnover": 0.5})

    report = NaNFutureLeakageProbe().check(_NoPanel(), spec)
    assert report.passed is False  # cannot probe -> not a pass
