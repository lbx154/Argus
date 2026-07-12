"""adata A-share FUNDAMENTAL data — point-in-time aligned factor panels.

The qlib ``cn_data_tushare`` dump ships only OHLCV, so every round-1 factor was
price/volume. This module adds the missing *fundamental* leg using
``adata.stock.finance.get_core_index``, which returns quarterly core financials
per stock **with a ``notice_date`` (公告日)** — the date the report became
public. We align each report point-in-time to the trading calendar: a report's
values are usable only on/after its ``notice_date`` and are forward-filled until
the next report supersedes them. That kills the look-ahead / restatement leakage
that naive ``report_date`` alignment would cause.

Output is a market-agnostic ``(T, S)`` panel keyed to the same ``dates``/``codes``
the ``factor_toolkit`` and ``qlib_cn`` engine already use, so a fundamental
factor (EP, BP, CFP, ...) composes with the price/volume factors and feeds the
same signal/backtest path — no separate code path.

adata: https://github.com/1nchaos/adata (专注A股行情+财务数据).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

#: adata core-index column -> our fundamental field name (per-share where noted).
_FIELD_SOURCE: dict[str, str] = {
    "eps": "basic_eps",            # 每股收益 (single-report; annualise/TTM upstream)
    "bps": "net_asset_ps",         # 每股净资产 (book value per share)
    "cfps": "oper_cf_ps",          # 每股经营现金流
    "gross_profit": "gross_profit",
    "net_profit": "net_profit_attr_sh",
    "revenue": "total_rev",
}

#: A fetcher maps a code -> a per-code core-index DataFrame (>= notice_date + the
#: source columns above). Default hits adata; tests inject a synthetic one.
Fetcher = Callable[[str], Any]


def _default_fetch(code: str) -> Any:
    """Fetch one code's quarterly core financial indicators via adata."""
    try:
        import adata  # lazy: on-line fetcher, not a declared dependency
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "adata_cn.fundamentals requires the 'adata' package — `pip install adata`"
        ) from exc
    return adata.stock.finance.get_core_index(stock_code=str(code))


def _to_adata_code(code: str) -> str:
    """qlib ``SZ000001`` / ``SH600519`` -> adata bare ``000001`` / ``600519``."""
    c = str(code).upper()
    if c[:2] in ("SZ", "SH", "BJ"):
        return c[2:]
    return c


def pit_align_field(
    reports: Any, dates: Sequence[Any], value_col: str
) -> np.ndarray:
    """PIT-align one report column to ``dates`` (length T) -> 1-D float array.

    For each trading date ``t`` the value is taken from the report with the
    LATEST ``notice_date <= t`` (forward-filled). Dates before the first report
    are NaN. ``reports`` is one stock's core-index frame.
    """
    import pandas as pd

    out = np.full(len(dates), np.nan)
    if reports is None or len(reports) == 0 or value_col not in reports.columns:
        return out
    df = reports.copy()
    df["notice_date"] = pd.to_datetime(df["notice_date"], errors="coerce")
    df = df.dropna(subset=["notice_date"]).sort_values("notice_date")
    if df.empty:
        return out
    vals = pd.to_numeric(df[value_col], errors="coerce").to_numpy(dtype=float)
    notice = df["notice_date"].to_numpy()
    d = pd.to_datetime(list(dates)).to_numpy()
    # index of the last notice_date <= each trade date (right-side searchsorted-1)
    idx = np.searchsorted(notice, d, side="right") - 1
    valid = idx >= 0
    out[valid] = vals[idx[valid]]
    return out


def load_fundamental_panel(
    codes: Sequence[str],
    dates: Sequence[Any],
    *,
    fields: Sequence[str] = ("eps", "bps", "cfps"),
    fetch: Fetcher | None = None,
) -> dict[str, np.ndarray]:
    """Build PIT-aligned ``(T, S)`` fundamental panels for ``codes`` over ``dates``.

    Returns ``{field: (T, S) array}`` for each requested field plus ``dates`` /
    ``codes``. Codes with no fundamentals are kept as all-NaN columns (so the
    panel stays aligned with the price panel's column order).
    """
    fetch = fetch or _default_fetch
    T, S = len(dates), len(codes)
    panels: dict[str, np.ndarray] = {f: np.full((T, S), np.nan) for f in fields}
    for j, code in enumerate(codes):
        try:
            reports = fetch(_to_adata_code(code))
        except Exception:  # noqa: BLE001 - one bad code must not sink the panel
            continue
        for f in fields:
            src = _FIELD_SOURCE.get(f)
            if src is None:
                continue
            panels[f][:, j] = pit_align_field(reports, dates, src)
    panels["dates"] = np.asarray(list(dates))
    panels["codes"] = tuple(str(c) for c in codes)
    return panels


def fundamental_factor(
    kind: str, fundamentals: dict[str, np.ndarray], close: np.ndarray
) -> np.ndarray:
    """Compose a signed fundamental factor from a PIT panel + aligned close.

    ``kind``: ``"ep"`` (earnings yield = eps/price, higher=cheaper=higher expected
    return), ``"bp"`` (book/price = bps/price), ``"cfp"`` (cashflow/price). All are
    "value" factors signed so a higher score means cheaper / higher expected
    forward return; scale-free since divided by price.
    """
    src = {"ep": "eps", "bp": "bps", "cfp": "cfps"}.get(kind)
    if src is None or src not in fundamentals:
        raise ValueError(f"unknown fundamental factor {kind!r}")
    num = np.asarray(fundamentals[src], dtype=float)
    px = np.asarray(close, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(px > 0, num / px, np.nan)
