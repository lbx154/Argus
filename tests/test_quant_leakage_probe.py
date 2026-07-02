"""The NaN-future leakage probe must FAIL a look-ahead engine (R3-3).

The earlier probe compared IC before/after masking forward_returns, but IC IS
Spearman(score, forward_returns) — masking nulls it for ANY engine, so the probe
passed unconditionally (a perfect look-ahead engine passed too). The fix watches
a score-derived, forward-independent metric (turnover) for INVARIANCE instead.
"""
from __future__ import annotations

import warnings

import numpy as np

from argus_skill.verticals.quant.backtest import BacktestSpec
from argus_skill.verticals.quant.factors import FactorSpec, InMemoryFactorRegistry
from argus_skill.verticals.quant.leakage_probe import NaNFutureLeakageProbe
from argus_skill.verticals.quant.reference_engine import (
    ToyBacktestEngine,
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


def test_all_nan_ic_returns_explicit_nan_without_runtime_warning():
    panel, registry, spec = _fixture()
    panel.forward_returns[:, :] = np.nan
    engine = ToyBacktestEngine(panel=panel, registry=registry)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = engine.run(spec)

    assert np.isnan(result.metrics["ic"])
    assert np.isnan(result.metrics["icir"])


def test_missing_panel_is_a_failing_noop():
    _panel, _registry, spec = _fixture()

    class _NoPanel:
        def run(self, spec):  # noqa: ARG002
            from argus_skill.verticals.quant.backtest import BacktestResult
            return BacktestResult(run_id="r", metrics={"turnover": 0.5})

    report = NaNFutureLeakageProbe().check(_NoPanel(), spec)
    assert report.passed is False  # cannot probe -> not a pass
