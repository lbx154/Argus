"""Tests for the adata A-share loader (integrations.adata_cn).

adata is an on-line fetcher (rate-limited/flaky and network-dependent), so the
panel-assembly logic is tested with an injected synthetic fetcher; a live smoke
test is included but skips when adata returns nothing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from argus_skill.verticals.quant.integrations.adata_cn import (
    forward_returns,
    load_ohlcv_panel,
    to_feature_panel,
)


def _fake_fetch(code, start, end, k_type, adjust):
    # 000404 returns nothing (simulates a delisted/empty code -> must be dropped)
    if code == "000404":
        return pd.DataFrame()
    n = 40
    dates = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    base = 10.0 + (int(code) % 7)
    close = base + np.cumsum(np.full(n, 0.05))
    return pd.DataFrame({
        "trade_date": dates, "open": close * 0.99, "high": close * 1.01,
        "low": close * 0.98, "close": close, "volume": 1e6,
        "amount": close * 1e6, "turnover_ratio": 0.5,
    })


def test_load_panel_shape_and_fields():
    panel = load_ohlcv_panel(
        ["000001", "600519", "000002"], start_date="2024-01-01", fetch=_fake_fetch
    )
    assert panel["close"].shape == (40, 3)
    assert panel["codes"] == ("000001", "600519", "000002")
    assert panel["dates"].shape == (40,)
    for f in ("open", "high", "low", "close", "volume", "turnover"):
        assert panel[f].shape == (40, 3)


def test_empty_codes_are_dropped():
    panel = load_ohlcv_panel(
        ["000001", "000404", "600519"], start_date="2024-01-01", fetch=_fake_fetch
    )
    assert panel["codes"] == ("000001", "600519")  # 000404 dropped
    assert panel["close"].shape[1] == 2


def test_all_empty_raises():
    with pytest.raises(ValueError):
        load_ohlcv_panel(["000404"], start_date="2024-01-01", fetch=_fake_fetch)


def test_forward_returns_no_lookahead():
    close = np.arange(1, 25, dtype=float).reshape(6, 4)
    fwd = forward_returns(close, horizon=1)
    assert fwd.shape == close.shape
    assert np.isnan(fwd[-1]).all()                       # last bar has no future
    np.testing.assert_allclose(fwd[0], close[1] / close[0] - 1.0)  # forward, not backward
    with pytest.raises(ValueError):
        forward_returns(close, horizon=0)


def test_to_feature_panel_runs_through_builder():
    panel = load_ohlcv_panel(["000001", "600519"], start_date="2024-01-01", fetch=_fake_fetch)
    toy_panel, registry = to_feature_panel(panel)
    assert toy_panel.factor_values.shape[2] == len(registry.factor_ids())
    assert toy_panel.forward_returns.shape == panel["close"].shape


@pytest.mark.parametrize("codes", [["000001"]])
def test_live_adata_smoke(codes):
    pytest.importorskip("adata")
    try:
        panel = load_ohlcv_panel(codes, start_date="2024-01-01")
    except Exception:
        pytest.skip("adata fetch failed (network / rate-limit)")
    if panel["close"].shape[0] == 0:
        pytest.skip("adata returned no rows (rate-limited)")
    assert panel["close"].shape[1] == len(panel["codes"])
