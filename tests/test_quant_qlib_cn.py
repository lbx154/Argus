"""Tests for the qlib_cn real backtest engine (integrations.qlib_cn).

qlib and a local ``cn_data_tushare`` dump are optional; the parts that need
neither (signal shaping, Protocol conformance) always run, and the live
backtest skips when qlib or the dump is absent.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from argus_skill.verticals.quant.backtest import (
    BacktestEngine,
    BacktestSpec,
    config_fingerprint,
    run_backtest,
)
from argus_skill.verticals.quant.integrations.qlib_cn import (
    QlibCnEngine,
    factor_to_signal,
)
from argus_skill.verticals.quant.integrations.qlib_cn import data as qdata
from argus_skill.verticals.quant.search_ledger import SearchLedger


def test_factor_to_signal_shape_and_dropna():
    import pandas as pd

    dates = pd.date_range("2024-01-01", periods=4, freq="B")
    codes = ["SZ000001", "SH600519"]
    fv = np.array([[1.0, 2.0], [np.nan, 3.0], [0.5, np.nan], [1.0, 1.0]])
    s = factor_to_signal(fv, dates, codes)
    assert list(s.index.names) == ["datetime", "instrument"]
    assert len(s) == 6  # 8 cells minus 2 NaN
    with pytest.raises(ValueError):
        factor_to_signal(fv, dates, ["only_one_code"])


def test_engine_satisfies_protocol():
    engine = QlibCnEngine(signal_provider=lambda spec: None)
    assert isinstance(engine, BacktestEngine)


def test_config_hash_covers_trial_params():
    engine = QlibCnEngine(signal_provider=lambda spec: None)
    base = BacktestSpec(run_id="x", factor_ids=["f"], params={})
    changed = BacktestSpec(
        run_id="x",
        factor_ids=["f"],
        params={"compute_forward_alignment": True},
    )
    assert engine._config_hash(base) != engine._config_hash(changed)


def test_config_fingerprint_canonicalizes_unordered_values():
    first = BacktestSpec(
        run_id="a",
        factor_ids=["f"],
        params={"groups": {"alpha", "beta", "gamma"}},
    )
    second = BacktestSpec(
        run_id="b",
        factor_ids=["f"],
        params={"groups": {"gamma", "alpha", "beta"}},
    )
    assert config_fingerprint(engine_name="test", spec=first) == config_fingerprint(
        engine_name="test",
        spec=second,
    )


def test_config_fingerprint_canonicalizes_numpy_scalars():
    numpy_spec = BacktestSpec(
        run_id="a",
        factor_ids=["f"],
        params={"count": np.int64(5), "enabled": np.bool_(True)},
    )
    native_spec = BacktestSpec(
        run_id="b",
        factor_ids=["f"],
        params={"count": 5, "enabled": True},
    )
    assert config_fingerprint(
        engine_name="test",
        spec=numpy_spec,
    ) == config_fingerprint(engine_name="test", spec=native_spec)
    extended = BacktestSpec(
        run_id="c",
        factor_ids=["f"],
        params={"threshold": np.longdouble("1.5")},
    )
    assert config_fingerprint(engine_name="test", spec=extended)


def test_empty_signal_becomes_error_row(tmp_path):
    # provider returns empty -> run() raises before qlib init -> run_backtest records an error row.
    engine = QlibCnEngine(signal_provider=lambda spec: None)
    ledger = SearchLedger(str(tmp_path / "L.jsonl"))
    res, row = run_backtest(engine, BacktestSpec(run_id="x", factor_ids=["f"]), ledger)
    assert res.status == "error"
    assert len(ledger) == 1 and ledger.verify_chain()


def _dump_available() -> bool:
    return os.path.isdir(qdata.DEFAULT_PROVIDER_URI) and os.path.isdir(
        os.path.join(qdata.DEFAULT_PROVIDER_URI, "features")
    )


def test_live_qlib_backtest(tmp_path):
    pytest.importorskip("qlib")
    if not _dump_available():
        pytest.skip("no local qlib cn_data dump")
    from argus_skill.verticals.quant.factor_toolkit.expression import expression_feature
    from argus_skill.verticals.quant.integrations.qlib_cn import make_toolkit_signal_provider

    try:
        prov = make_toolkit_signal_provider(
            universe="csi500", start="2024-01-01", end="2024-04-30",
            feature=expression_feature("rev5", "-1*rank(ts_delta(close,5))",
                                        direction=1.0, description="5d reversal"),
        )
    except Exception as exc:  # data/init issue in this environment
        pytest.skip(f"qlib data load failed: {exc}")
    engine = QlibCnEngine(signal_provider=prov, topk=30, n_drop=3)
    ledger = SearchLedger(str(tmp_path / "run" / "SEARCH_LEDGER.jsonl"))
    res, _ = run_backtest(
        engine, BacktestSpec(run_id="rev5", factor_ids=["rev5"], window="test"), ledger
    )
    assert res.status == "ok"
    assert {"sharpe", "annualized_return", "max_drawdown"} <= set(res.metrics)
    assert ledger.verify_chain()


def test_live_amount_field_loads():
    """load_qlib_ohlcv must expose 'amount' — the A-share liquidity factors need it."""
    pytest.importorskip("qlib")
    if not _dump_available():
        pytest.skip("no local qlib cn_data dump")
    try:
        insts = qdata.list_universe("csi500", "2024-01-01", "2024-03-31")[:20]
        panel = qdata.load_qlib_ohlcv("csi500", "2024-01-02", "2024-03-29", instruments=insts)
    except Exception as exc:
        pytest.skip(f"qlib data load failed: {exc}")
    assert "amount" in panel and panel["amount"].shape == panel["close"].shape


def test_live_oos_boundary_does_not_crash(tmp_path):
    """A backtest whose signal runs to the dump's LAST calendar day must be
    capped (leave a next-day settlement bar) instead of raising IndexError."""
    pytest.importorskip("qlib")
    if not _dump_available():
        pytest.skip("no local qlib cn_data dump")
    from argus_skill.verticals.quant.factor_toolkit.expression import expression_feature
    from argus_skill.verticals.quant.integrations.qlib_cn import make_toolkit_signal_provider

    qdata.qlib_init()
    from qlib.data import D

    last_day = str(D.calendar()[-1])[:10]  # the dump boundary that used to crash
    try:
        prov = make_toolkit_signal_provider(
            universe="csi500", start="2025-06-01", end=last_day,
            feature=expression_feature("amihud", "rank(ts_mean(abs(returns)/amount, 20))",
                                        direction=1.0, description="Amihud illiquidity"),
        )
    except Exception as exc:
        pytest.skip(f"qlib data load failed: {exc}")
    engine = QlibCnEngine(signal_provider=prov, topk=30, n_drop=3)
    ledger = SearchLedger(str(tmp_path / "run" / "SEARCH_LEDGER.jsonl"))
    res, _ = run_backtest(
        engine, BacktestSpec(run_id="amihud-oos", factor_ids=["amihud"], window="test",
                             is_out_of_sample=True), ledger)
    assert res.status == "ok"  # no IndexError at the calendar boundary
    assert any("capped" in w for w in res.warnings)
