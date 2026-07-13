"""Deterministic tests for K-line charting (headless Agg backend, no network)."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from argus_skill.verticals.quant.charting import _prep, candlestick_chart


def _ohlcv(n=90, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, n))
    dates = pd.bdate_range("2024-01-01", periods=n)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(1e6, 1e7, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=dates)


def test_prep_normalises_columns_and_index():
    df = _prep(_ohlcv(30))
    assert list(df.columns[:4]) == ["Open", "High", "Low", "Close"]
    assert isinstance(df.index, pd.DatetimeIndex)


def test_prep_missing_columns_raises():
    with pytest.raises(ValueError):
        _prep(pd.DataFrame({"open": [1.0], "close": [2.0]}))


def test_candlestick_chart_writes_png(tmp_path):
    out = str(tmp_path / "k.png")
    p = candlestick_chart(_ohlcv(), out, title="TEST", mavs=(5, 20))
    assert p == out and os.path.getsize(out) > 1000  # a real PNG was written


def test_candlestick_with_signal_and_markers(tmp_path):
    df = _ohlcv()
    out = str(tmp_path / "k2.png")
    sig = pd.Series(np.linspace(-1, 1, len(df)), index=df.index)
    buy = [df.index[10], df.index[40]]
    sell = [df.index[25], df.index[60]]
    candlestick_chart(df, out, signal=sig, buy=buy, sell=sell, mavs=(5,))
    assert os.path.getsize(out) > 1000
