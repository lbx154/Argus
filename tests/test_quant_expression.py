"""Tests for the alpha expression DSL (factor_toolkit.expression)."""
from __future__ import annotations

import numpy as np
import pytest

from argus_skill.verticals.quant import factor_toolkit as ft
from argus_skill.verticals.quant.backtest import BacktestSpec
from argus_skill.verticals.quant.executor import ForcingExecutor
from argus_skill.verticals.quant.factor_toolkit.expression import (
    ExpressionError,
    available_operators,
    evaluate,
    expression_feature,
)
from argus_skill.verticals.quant.reference_engine import ToyBacktestEngine
from argus_skill.verticals.quant.search_ledger import SearchLedger


def _fields(T=80, S=20, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, (T, S)), axis=0)
    return {
        "close": close, "high": close * 1.01, "low": close * 0.99, "open": close,
        "volume": rng.uniform(1e6, 5e6, (T, S)), "amount": close * rng.uniform(1e6, 5e6, (T, S)),
    }


@pytest.mark.parametrize("expr", [
    "rank(ts_delta(close, 5))",
    "-1 * ts_decay_linear(close / vwap, 10)",
    "rank(ts_std(returns, 20)) - rank(ts_mean(volume, 5))",
    "zscore(ts_corr(close, volume, 10))",
    "sign(ts_delta(close, 1)) * power(abs(returns), 0.5)",
    "ts_rank(close, 10) + ts_argmax(high, 5)",
])
def test_expressions_evaluate_to_panel_shape(expr):
    f = _fields()
    out = evaluate(expr, f)
    assert out.shape == f["close"].shape


def test_rank_is_bounded_and_cross_sectional():
    f = _fields()
    r = evaluate("rank(close)", f)
    valid = r[~np.isnan(r)]
    assert valid.min() >= 0.0 and valid.max() <= 1.0


def test_aliases_resolve():
    f = _fields()
    np.testing.assert_allclose(
        evaluate("delta(close, 3)", f), evaluate("ts_delta(close, 3)", f), equal_nan=True
    )


def test_derived_fields():
    f = _fields()
    assert evaluate("vwap", f).shape == f["close"].shape
    ret = evaluate("returns", f)
    assert np.isnan(ret[0]).all()  # first row has no prior close


def test_unknown_field_and_operator_raise():
    f = _fields()
    with pytest.raises(ExpressionError):
        evaluate("rank(nonexistent_field)", f)
    with pytest.raises(ExpressionError):
        evaluate("bogus_op(close, 5)", f)


@pytest.mark.parametrize("bad", [
    "__import__('os').system('echo hi')",
    "close.__class__",
    "close[0]",
    "(lambda: 1)()",
    "open_file('x')",
    "[c for c in close]",
    "close if True else open",
])
def test_disallowed_constructs_rejected(bad):
    with pytest.raises(ExpressionError):
        evaluate(bad, _fields())


def test_syntax_error_is_wrapped():
    with pytest.raises(ExpressionError):
        evaluate("rank(close", _fields())  # unbalanced paren


def test_available_operators_nonempty():
    ops = available_operators()
    assert "rank" in ops and "ts_decay_linear" in ops and "ts_corr" in ops


def test_expression_feature_runs_through_builder_and_ledger(tmp_path):
    f = _fields()
    feats = [
        expression_feature("mom5", "rank(ts_delta(close,5))", direction=1.0, description="5d momentum"),
        expression_feature("vwap_rev", "-1*ts_decay_linear(close/vwap,10)", direction=1.0, description="vwap reversal"),
    ]
    rng = np.random.default_rng(1)
    fwd = rng.normal(0.001, 0.02, f["close"].shape)
    panel, registry = ft.build_feature_panel(f, fwd, features=feats)
    assert set(registry.factor_ids()) == {"mom5", "vwap_rev"}
    for fid in registry.factor_ids():
        assert registry.get(fid).description.strip()

    engine = ToyBacktestEngine(panel=panel, registry=registry)
    ledger = SearchLedger(str(tmp_path / "L.jsonl"))
    ex = ForcingExecutor(engine=engine, ledger=ledger)
    for fid in registry.factor_ids():
        res, _ = ex.submit(BacktestSpec(run_id=f"e-{fid}", factor_ids=[fid], weighting="single", window="test"))
        assert res.status == "ok" and "ic" in res.metrics
    assert ledger.verify_chain()
