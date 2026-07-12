"""Deterministic regression tests for the qlib_cn OOS boundary cap + runner.

The live version (``test_quant_qlib_cn.py::test_live_oos_boundary_does_not_crash``)
needs a real qlib dump and skips without one. These tests fake qlib via
``sys.modules`` injection so the *exact* off-by-one that killed the
``quarantined_test`` OOS trials — a backtest whose window ends on the dump's
last calendar day, which qlib then indexes one past while settling the final
rebalance (``IndexError: index N is out of bounds for axis 0 with size N``) —
is guarded on every CI run, dump or no dump.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from argus_skill.verticals.quant.backtest import BacktestSpec, run_backtest
from argus_skill.verticals.quant.integrations.qlib_cn.engine import QlibCnEngine
from argus_skill.verticals.quant.integrations.qlib_cn.runner import (
    FactorTrial,
    _slice_signal_provider,
)
from argus_skill.verticals.quant.search_ledger import SearchLedger


def _signal(dates, codes=("SH600000", "SZ000001")):
    """A qlib-style (datetime, instrument) score Series over ``dates``."""
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(list(dates)), list(codes)], names=["datetime", "instrument"]
    )
    return pd.Series(np.arange(len(idx), dtype=float), index=idx, name="score")


def _install_fake_qlib(monkeypatch, calendar, capture):
    """Inject a minimal fake qlib whose ``backtest`` records the end it was
    asked for and whose ``D.calendar()`` returns ``calendar``."""
    qlib = types.ModuleType("qlib")
    qlib.init = lambda **_k: None

    data_mod = types.ModuleType("qlib.data")

    class _D:
        @staticmethod
        def calendar(*_a, **_k):
            return list(calendar)

    data_mod.D = _D

    bt_mod = types.ModuleType("qlib.backtest")

    def _backtest(*, start_time, end_time, strategy, benchmark, account, executor, exchange_kwargs):
        capture["start"] = str(start_time)[:10]
        capture["end"] = str(end_time)[:10]
        capture["exchange_kwargs"] = dict(exchange_kwargs)
        # A tiny positive-return report keyed 1day, shaped like qlib's output.
        idx = pd.to_datetime([d for d in calendar if start_time <= str(d)[:10] <= end_time])
        report = pd.DataFrame({"return": np.full(len(idx), 0.001)}, index=idx)
        return {"1day": (report, None)}, None

    bt_mod.backtest = _backtest

    contrib = types.ModuleType("qlib.contrib")
    ev_mod = types.ModuleType("qlib.contrib.evaluate")
    ev_mod.risk_analysis = lambda ret, *_a, **_k: pd.DataFrame(
        {"risk": {"information_ratio": 1.0, "annualized_return": 0.1, "max_drawdown": -0.05}}
    )
    st_mod = types.ModuleType("qlib.contrib.strategy")

    class _Topk:
        def __init__(self, **_k):
            pass

    st_mod.TopkDropoutStrategy = _Topk

    for name, mod in [
        ("qlib", qlib), ("qlib.data", data_mod), ("qlib.backtest", bt_mod),
        ("qlib.contrib", contrib), ("qlib.contrib.evaluate", ev_mod),
        ("qlib.contrib.strategy", st_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)


def test_end_on_last_calendar_day_is_capped(monkeypatch, tmp_path):
    """Signal running to the dump's last day -> end capped to cal[-2], warned,
    and qlib is never asked to settle past the calendar (no IndexError)."""
    cal = pd.bdate_range("2026-05-01", periods=15)  # last day is the boundary
    capture: dict[str, str] = {}
    _install_fake_qlib(monkeypatch, cal, capture)

    sig = _signal(cal[-8:])  # signal runs right up to the last calendar day
    engine = QlibCnEngine(signal_provider=lambda _s: sig, provider_uri="/fake/dump")
    ledger = SearchLedger(str(tmp_path / "L.jsonl"))
    res, _row = run_backtest(engine, BacktestSpec(run_id="oos", factor_ids=["f"],
                                                  is_out_of_sample=True), ledger)

    assert res.status == "ok"  # would be an IndexError error-row without the cap
    assert capture["end"] == str(cal[-2])[:10]  # capped one trading day inside
    assert capture["end"] != str(cal[-1])[:10]
    assert any("capped" in w for w in res.warnings)
    assert ledger.verify_chain()


def test_cost_model_metadata_is_recorded(monkeypatch, tmp_path):
    """Successful qlib-cn rows must expose the exact predeclared base cost map."""
    cal = pd.bdate_range("2024-01-02", periods=20)
    capture: dict[str, object] = {}
    _install_fake_qlib(monkeypatch, cal, capture)

    sig = _signal(cal[3:15])
    engine = QlibCnEngine(signal_provider=lambda _s: sig, provider_uri="/fake/dump")
    ledger = SearchLedger(str(tmp_path / "L.jsonl"))
    res, row = run_backtest(
        engine,
        BacktestSpec(
            run_id="cost-map",
            factor_ids=["rev5"],
            window="validation",
            is_out_of_sample=False,
            universe="local qlib CSI500 instrument sample",
        ),
        ledger,
    )

    assert res.status == "ok"
    assert capture["exchange_kwargs"] == {
        "freq": "day",
        "limit_threshold": 0.095,
        "deal_price": "close",
        "open_cost": 0.0005,
        "close_cost": 0.0015,
        "min_cost": 5.0,
        "impact_cost": 0.0005,
    }
    assert row.payload["cost_model_id"] == "plan/COST_MODEL.json:base"
    assert row.payload["net_of_cost"] is True
    assert row.payload["buy_cost_bps"] == 5.0
    assert row.payload["sell_cost_bps"] == 15.0
    assert row.payload["minimum_trade_cost_cny"] == 5.0
    assert row.payload["slippage_bps_per_side"] == 5.0
    assert row.payload["limit_up_down_nontradable"] is True
    assert row.payload["suspended_or_missing_bar_nontradable"] is True
    assert row.payload["next_bar_execution_required"] is True
    assert row.payload["metadata"]["qlib_exchange_kwargs"]["impact_cost"] == 0.0005
    assert ledger.verify_chain()


def test_end_inside_calendar_is_not_capped(monkeypatch, tmp_path):
    """A window ending well before the boundary is left untouched (no false cap)."""
    cal = pd.bdate_range("2026-05-01", periods=20)
    capture: dict[str, str] = {}
    _install_fake_qlib(monkeypatch, cal, capture)

    sig = _signal(cal[3:8])  # ends far from the last calendar day
    engine = QlibCnEngine(signal_provider=lambda _s: sig, provider_uri="/fake/dump")
    ledger = SearchLedger(str(tmp_path / "L.jsonl"))
    res, _row = run_backtest(engine, BacktestSpec(run_id="is", factor_ids=["f"]), ledger)

    assert res.status == "ok"
    assert capture["end"] == str(cal[7])[:10]  # unchanged
    assert not any("capped" in w for w in res.warnings)


def test_single_trial_expression_is_the_raw_signed_formula():
    tr = FactorTrial("F", {"F": "-1 * (close / delay(close, 5) - 1)"})
    assert not tr.is_combo
    assert tr.expression() == "-1 * (close / delay(close, 5) - 1)"


def test_combo_expression_is_weighted_sum_of_standardized_factors():
    tr = FactorTrial(
        "COMBO", {"B": "close", "A": "amount"}, {"A": 0.7, "B": 0.3}, standardize="zscore"
    )
    assert tr.is_combo
    # deterministic sorted-by-factor-id term order
    assert tr.expression() == "(0.7 * zscore(amount)) + (0.3 * zscore(close))"


def test_combo_requires_weights_for_every_member():
    with pytest.raises(ValueError):
        FactorTrial("C", {"A": "amount", "B": "close"}, {"A": 1.0})  # missing B


def test_slice_provider_drops_warmup_history():
    """The warm-up dates loaded before the test window are sliced off the signal."""
    dates = pd.bdate_range("2024-12-25", periods=10)  # spans across 2025-01-02
    base = lambda _s: _signal(dates)
    sliced = _slice_signal_provider(base, "2025-01-02")(BacktestSpec(run_id="x", factor_ids=["f"]))
    kept = sliced.index.get_level_values("datetime")
    assert kept.min() >= pd.Timestamp("2025-01-02")
    assert kept.max() == pd.Timestamp(dates[-1])


def test_slice_provider_raises_when_window_empty():
    dates = pd.bdate_range("2024-01-01", periods=5)
    base = lambda _s: _signal(dates)
    with pytest.raises(ValueError):
        _slice_signal_provider(base, "2030-01-01")(BacktestSpec(run_id="x", factor_ids=["f"]))
