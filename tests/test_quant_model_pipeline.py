"""Deterministic tests for the Alpha360+fundamental model pipeline (no network/dump).

Covers the new cross-family model path: the YTD->TTM de-seasonalisation, the PIT
fundamental FEATURE frame, the read-through cache seam, time-ordered splits, the
GBDT train/predict, and the prediction->qlib-signal backtest (fake qlib, so the
ledger/boundary contract is exercised dump-free). Mirrors the fake-qlib pattern in
``test_quant_qlib_cn_oos_boundary.py`` and the synthetic-fetch pattern in
``test_quant_adata_fundamentals.py``.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from argus_skill.verticals.quant.integrations.adata_cn.cache import cached_fetcher
from argus_skill.verticals.quant.integrations.adata_cn.fundamentals import (
    fundamental_feature_frame,
    ytd_to_ttm,
)
from argus_skill.verticals.quant.integrations.qlib_cn.features import (
    cross_sectional_normalize,
    time_split,
)
from argus_skill.verticals.quant.integrations.qlib_cn.model import (
    backtest_predictions,
    rolling_retrain_predict,
    train_predict,
)
from argus_skill.verticals.quant.search_ledger import SearchLedger


def _reports():
    """Two fiscal years of YTD-cumulative quarterly reports for one code."""
    return pd.DataFrame(
        {
            "report_date": ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"],
            "notice_date": ["2024-04-25", "2024-08-25", "2024-10-25", "2025-03-21", "2025-04-25"],
            "basic_eps": [0.5, 1.1, 1.7, 2.4, 0.6],  # YTD cumulative
            "oper_cf_ps": [0.2, 0.5, 0.9, 1.6, 0.3],  # YTD cumulative
            "net_asset_ps": [10.0, 10.5, 11.0, 11.5, 12.0],  # level
            "net_profit_yoy_gr": [8.0, 9.0, 10.0, 11.0, 12.0],
            "total_rev_yoy_gr": [5.0, 6.0, 7.0, 8.0, 9.0],
        }
    )


def test_ytd_to_ttm_recovers_trailing_year():
    ttm = ytd_to_ttm(_reports(), "basic_eps")
    # single-Q 2024: .5/.6/.6/.7 ; TTM(2024Q4)=annual=2.4 ; TTM(2025Q1)=.6+.6+.7+.6=2.5
    assert [pd.isna(x) for x in ttm[:3]] == [True, True, True]  # <4 trailing quarters
    assert ttm.iloc[3] == pytest.approx(2.4)
    assert ttm.iloc[4] == pytest.approx(2.5)


def test_fundamental_feature_frame_pit_ttm_and_growth():
    dates = pd.to_datetime(
        ["2025-04-01", "2025-05-01"]
    )  # sees the 2024-annual report (notice 2025-03-21)
    codes = ["SZ000001"]
    close = np.array([[10.0], [10.0]])
    frame = fundamental_feature_frame(codes, dates.to_numpy(), close, fetch=lambda _c: _reports())
    # rich panel: value + quality + growth + accruals all present
    assert {"ep", "bp", "cfp", "np_yoy", "rev_yoy", "roe", "accruals"} <= set(frame.columns)
    row = frame.xs("SZ000001", level="instrument").iloc[0]
    assert row["ep"] == pytest.approx(2.4 / 10.0)  # TTM eps 2.4 / price 10
    assert row["bp"] == pytest.approx(11.5 / 10.0)  # book/price level
    assert row["np_yoy"] == pytest.approx(11.0)  # PIT: 2024-annual growth
    # pre-first-visible-report dates are NaN (no look-ahead)
    early = fundamental_feature_frame(
        codes,
        pd.to_datetime(["2024-01-01"]).to_numpy(),
        np.array([[10.0]]),
        fetch=lambda _c: _reports(),
    )
    assert np.isnan(early.iloc[0]["ep"])


def test_cached_fetcher_read_through(tmp_path):
    calls = {"n": 0}

    def inner(code):
        calls["n"] += 1
        return pd.DataFrame({"notice_date": ["2024-04-25"], "basic_eps": [1.0]})

    fetch = cached_fetcher(str(tmp_path / "cache"), inner=inner)
    a = fetch("000001")
    b = fetch("000001")  # served from disk
    assert calls["n"] == 1  # inner called exactly once
    assert (tmp_path / "cache" / "000001.pkl").exists()
    pd.testing.assert_frame_equal(a, b)


def test_time_split_ordered_and_disjoint():
    idx = pd.MultiIndex.from_product(
        [pd.date_range("2022-01-01", "2022-12-31", freq="D"), ["A", "B"]],
        names=["datetime", "instrument"],
    )
    s = time_split(
        idx,
        train=("2022-01-01", "2022-06-30"),
        valid=("2022-07-01", "2022-09-30"),
        test=("2022-10-01", "2022-12-31"),
    )
    assert s["train"].sum() and s["valid"].sum() and s["test"].sum()
    assert not (s["train"] & s["valid"]).any()
    assert not (s["valid"] & s["test"]).any()
    # test window is strictly the latest dates (a real future)
    dt = idx.get_level_values("datetime")
    assert dt[s["test"]].min() > dt[s["train"]].max()


def _synthetic_xy(seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=180, freq="B")
    codes = [f"C{i:02d}" for i in range(20)]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "instrument"])
    f1 = rng.normal(size=len(idx))
    f2 = rng.normal(size=len(idx))
    y = pd.Series(2.0 * f1 - f2 + 0.1 * rng.normal(size=len(idx)), index=idx, name="label")
    X = pd.DataFrame(
        {"a360_f1": f1, "a360_f2": f2, "fund_ep": rng.normal(size=len(idx))}, index=idx
    ).astype("float32")
    return X, y, dates


def test_train_predict_learns_and_reports():
    X, y, dates = _synthetic_xy()
    splits = time_split(
        X.index,
        train=(str(dates[0].date()), str(dates[100].date())),
        valid=(str(dates[101].date()), str(dates[140].date())),
        test=(str(dates[141].date()), str(dates[-1].date())),
    )
    booster, pred, info = train_predict(X, y, splits, num_boost_round=100, early_stopping=20)
    assert info["n_test"] == int(splits["test"].sum())
    assert 0.0 <= info["fund_gain_fraction"] <= 1.0
    # the model recovered the y = 2*f1 - f2 signal on the held-out future
    truth = y.reindex(pred.index)
    assert pred.corr(truth) > 0.5


def _install_fake_qlib(monkeypatch, calendar):
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
        idx = pd.to_datetime([d for d in calendar if start_time <= str(d)[:10] <= end_time])
        return {"1day": (pd.DataFrame({"return": np.full(len(idx), 0.001)}, index=idx), None)}, None

    bt_mod.backtest = _backtest
    contrib = types.ModuleType("qlib.contrib")
    ev_mod = types.ModuleType("qlib.contrib.evaluate")
    ev_mod.risk_analysis = lambda ret, *_a, **_k: pd.DataFrame(
        {"risk": {"information_ratio": 1.0, "annualized_return": 0.1, "max_drawdown": -0.05}}
    )
    st_mod = types.ModuleType("qlib.contrib.strategy")
    st_mod.TopkDropoutStrategy = type("_T", (), {"__init__": lambda self, **_k: None})
    for name, mod in [
        ("qlib", qlib),
        ("qlib.data", data_mod),
        ("qlib.backtest", bt_mod),
        ("qlib.contrib", contrib),
        ("qlib.contrib.evaluate", ev_mod),
        ("qlib.contrib.strategy", st_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)


def test_backtest_predictions_ledgers_a_model_row(monkeypatch, tmp_path):
    cal = pd.bdate_range("2025-01-01", periods=30)
    _install_fake_qlib(monkeypatch, cal)
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(cal[-6:]), ["SH600000", "SZ000001"]], names=["datetime", "instrument"]
    )
    pred = pd.Series(np.arange(len(idx), dtype=float), index=idx, name="score")
    ledger = SearchLedger(str(tmp_path / "L.jsonl"))
    res, row = backtest_predictions(
        pred,
        universe="csi500",
        ledger=ledger,
        run_id="oos-m",
        window_label="oos_test",
        forward_return_label_end="2025-02-10",
        model_id="alpha360_fund",
        provider_uri="/fake/dump",  # never poison _QLIB_INITED for the real dump path
        extra_params={"compute_forward_alignment": False},  # skip qlib price re-query in the fake
    )
    assert res.status == "ok"
    assert row.payload["is_out_of_sample"] is True
    assert row.payload["factor_ids"] == ["alpha360_fund"]
    assert row.payload["params"]["model_id"] == "alpha360_fund"
    assert ledger.verify_chain()


def test_cross_sectional_normalize_rank_and_zscore():
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(["2024-01-01", "2024-01-02"]), ["A", "B", "C", "D"]],
        names=["datetime", "instrument"],
    )
    X = pd.DataFrame({"f": [1.0, 2, 3, 4, 40, 30, 20, 10]}, index=idx)
    z = cross_sectional_normalize(X, method="rank")
    day1 = z.xs(pd.Timestamp("2024-01-01"), level="datetime")["f"].tolist()
    assert day1 == pytest.approx([-0.25, 0.0, 0.25, 0.5])  # per-day pct-rank (rank/n) centred
    zz = cross_sectional_normalize(X, method="zscore")
    assert abs(zz.xs(pd.Timestamp("2024-01-02"), level="datetime")["f"].sum()) < 1e-9


def test_forward_return_label_math(monkeypatch):
    import argus_skill.verticals.quant.integrations.qlib_cn.features as feat

    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
    close = np.array([[10.0, 100.0], [11.0, 110.0], [12.0, 99.0], [13.0, 101.0]])
    monkeypatch.setattr(
        feat._data,
        "load_qlib_ohlcv",
        lambda *a, **k: {"close": close, "dates": dates.to_numpy(), "codes": ("A", "B")},
    )
    lab = feat.forward_return_label("csi500", "2024-01-01", "2024-01-04", 2)
    assert lab.loc[(pd.Timestamp("2024-01-01"), "A")] == pytest.approx(12.0 / 10.0 - 1.0)
    assert np.isnan(lab.loc[(pd.Timestamp("2024-01-03"), "A")])  # no t+2 bar


def test_rolling_retrain_predict_rolls_expanding_no_leakage():
    X, y, dates = _synthetic_xy(seed=3)
    pred, windows = rolling_retrain_predict(
        X,
        y,
        family="gbdt",
        config={"num_boost_round": 40},
        oos_start=str(dates[120].date()),
        oos_end=str(dates[-1].date()),
        step_days=20,
        purge_days=1,
        min_train_days=60,
        seed=0,
    )
    assert len(windows) >= 2  # retrained multiple times
    assert [w["train_days"] for w in windows] == sorted(
        w["train_days"] for w in windows
    )  # expanding
    for w in windows:  # trains strictly before it predicts
        assert w["retrain_at"] <= w["predict_from"]
    assert pred.index.get_level_values("datetime").min() >= dates[120]
