"""Deterministic tests for the adata PIT fundamental alignment (no network).

``pit_align_field`` must (a) only expose a report on/after its ``notice_date``
(no look-ahead), (b) forward-fill until the next report, and (c) leave dates
before the first report as NaN. These guard the exact leakage the PIT design
exists to prevent, using an injected synthetic fetcher.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from argus_skill.verticals.quant.integrations.adata_cn.fundamentals import (
    _to_adata_code,
    fundamental_factor,
    load_fundamental_panel,
    pit_align_field,
)


def _reports():
    # two reports; note the SECOND report's notice_date is what gates exposure.
    return pd.DataFrame(
        {
            "report_date": ["2023-12-31", "2024-03-31"],
            "notice_date": ["2024-03-15", "2024-04-25"],
            "basic_eps": [1.0, 1.5],
            "net_asset_ps": [10.0, 11.0],
        }
    )


def test_pit_no_lookahead_and_forward_fill():
    dates = pd.to_datetime(
        ["2024-03-01", "2024-03-15", "2024-04-01", "2024-04-25", "2024-05-10"]
    )
    got = pit_align_field(_reports(), dates, "basic_eps")
    # before first notice -> NaN; on notice -> value; forward-filled; second
    # report only after 2024-04-25.
    assert np.isnan(got[0])          # 03-01 < first notice 03-15
    assert got[1] == 1.0             # 03-15 == first notice
    assert got[2] == 1.0             # forward-filled
    assert got[3] == 1.5             # 04-25 == second notice
    assert got[4] == 1.5


def test_load_panel_and_factor_with_injected_fetch():
    dates = pd.to_datetime(["2024-04-01", "2024-05-10"])
    codes = ["SZ000001", "SH600000"]
    fund = load_fundamental_panel(
        codes, dates, fields=("eps", "bps"), fetch=lambda _c: _reports()
    )
    assert fund["eps"].shape == (2, 2)
    assert fund["bps"][1, 0] == 11.0  # 05-10 sees the second report
    close = np.array([[10.0, 20.0], [10.0, 20.0]])
    bp = fundamental_factor("bp", fund, close)
    assert bp[1, 0] == 11.0 / 10.0    # book-to-price
    assert np.isfinite(bp).all()


def test_code_normalisation():
    assert _to_adata_code("SZ000001") == "000001"
    assert _to_adata_code("SH600519") == "600519"
    assert _to_adata_code("000001") == "000001"
