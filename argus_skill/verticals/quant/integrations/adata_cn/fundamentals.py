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


#: adata YoY-growth columns usable as features as-is (already season-comparable
#: rates, so no TTM/de-seasonalisation needed).
_GROWTH_SOURCE: dict[str, str] = {
    "np_yoy": "net_profit_yoy_gr",
    "rev_yoy": "total_rev_yoy_gr",
}


def ytd_to_ttm(reports: Any, src_col: str) -> Any:
    """Trailing-twelve-month series from a YTD-cumulative quarterly column.

    A-share ``basic_eps`` / ``oper_cf_ps`` are reported **year-to-date** cumulative
    (Q3 = 9-month, annual = 12-month), so using them raw injects a mechanical
    quarter-of-year seasonality a model would mistake for signal. This recovers the
    single-quarter flow (``YTD(q) - YTD(q-1)``, with Q1 = YTD(Q1)) and sums the
    trailing four quarters (all four required, else NaN). Returns a pandas Series
    aligned to ``reports.index``.
    """
    import numpy as np
    import pandas as pd

    df = reports
    rd = pd.to_datetime(df["report_date"], errors="coerce")
    val = pd.to_numeric(df[src_col], errors="coerce")
    q = rd.dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})
    qidx = rd.dt.year * 4 + (q - 1)  # global quarter ordinal (Q1 -> % 4 == 0)
    lut: dict[int, float] = {
        int(qi): float(v)
        for qi, v in zip(qidx, val)
        if pd.notna(qi) and pd.notna(v)
    }

    def single_q(qi: int) -> float:
        v = lut.get(qi)
        if v is None:
            return np.nan
        if qi % 4 == 0:  # Q1 YTD is already the single quarter
            return v
        prev = lut.get(qi - 1)
        return v - prev if prev is not None else np.nan

    sq = {qi: single_q(qi) for qi in lut}
    out = np.full(len(df), np.nan)
    for i, qi in enumerate(qidx.to_numpy()):
        if pd.isna(qi):
            continue
        qi = int(qi)
        terms = [sq.get(qi - k) for k in range(4)]
        if all(t is not None and not pd.isna(t) for t in terms):
            out[i] = float(sum(terms))  # type: ignore[arg-type]
    return pd.Series(out, index=df.index)


def fundamental_feature_frame(
    codes: Sequence[str],
    dates: Sequence[Any],
    close: np.ndarray,
    *,
    fetch: Fetcher | None = None,
    ttm: bool = True,
) -> Any:
    """PIT-aligned fundamental FEATURES as a ``(datetime, instrument)`` DataFrame.

    A ~21-factor cross-family panel from adata's core-index, PIT-aligned by
    ``notice_date``, spanning:

    * **value** — ``ep`` / ``cfp`` / ``ep_ng`` (per-share earnings/cashflow ÷ price,
      TTM by default) and ``bp`` (book ÷ price, a level);
    * **quality / profitability** — ``roe`` / ``roa`` / ``gross_margin`` /
      ``net_margin`` / ``cf_to_rev`` (cash conversion) / ``accruals``
      (earnings-minus-cash per book, a low-quality flag);
    * **growth** — revenue & profit YoY/QoQ (``rev_yoy`` / ``np_yoy`` /
      ``np_ng_yoy`` / ``rev_qoq`` / ``np_qoq``);
    * **balance-sheet health** — ``asset_liab`` / ``curr_ratio`` / ``quick_ratio``
      / ``asset_turn`` / ``inv_turn`` / ``recv_turn``.

    Reported ratios are fed as-is (already scale-free); only per-share flows are
    TTM'd. Built on the shared dates×codes grid and melted to the
    ``(datetime, instrument)`` MultiIndex so it merges 1:1 into the model matrix.
    For a MODEL these factors are unsigned — the model learns each direction.
    Missing codes/fields stay all-NaN (kept for alignment).
    """
    import numpy as np
    import pandas as pd

    fetch = fetch or _default_fetch
    T = len(dates)
    close = np.asarray(close, dtype=float)
    value_ttm = {"ep": "basic_eps", "cfp": "oper_cf_ps", "ep_ng": "non_gaap_eps"}
    value_level = {"bp": "net_asset_ps"}
    pit_ratios = {
        "roe": "roe_wtd", "roa": "roa_wtd", "gross_margin": "gross_margin",
        "net_margin": "net_margin", "cf_to_rev": "oper_cf_to_rev",
        "asset_liab": "asset_liab_ratio", "curr_ratio": "curr_ratio",
        "quick_ratio": "quick_ratio", "asset_turn": "total_asset_turn_rate",
        "inv_turn": "inv_turn_rate", "recv_turn": "acct_recv_turn_rate",
        "rev_yoy": "total_rev_yoy_gr", "np_yoy": "net_profit_yoy_gr",
        "np_ng_yoy": "non_gaap_net_profit_yoy_gr", "rev_qoq": "total_rev_qoq_gr",
        "np_qoq": "net_profit_qoq_gr",
    }
    feats = list(value_ttm) + list(value_level) + list(pit_ratios) + ["accruals"]
    panels = {f: np.full((T, len(codes)), np.nan) for f in feats}

    for j, code in enumerate(codes):
        try:
            reports = fetch(_to_adata_code(code))
        except Exception:  # noqa: BLE001 - one bad code must not sink the panel
            continue
        if reports is None or len(reports) == 0 or "notice_date" not in reports.columns:
            continue
        r = reports.copy()
        px = close[:, j]
        # TTM the per-share flow columns once
        use_of = {}
        for out, src in value_ttm.items():
            use = src
            if ttm and src in r.columns:
                r[f"_{src}_ttm"] = ytd_to_ttm(r, src)
                use = f"_{src}_ttm"
            use_of[out] = use
        with np.errstate(divide="ignore", invalid="ignore"):
            for out in value_ttm:
                if use_of[out] in r.columns:
                    num = pit_align_field(r, dates, use_of[out])
                    panels[out][:, j] = np.where(px > 0, num / px, np.nan)
            for out, src in value_level.items():
                if src in r.columns:
                    num = pit_align_field(r, dates, src)
                    panels[out][:, j] = np.where(px > 0, num / px, np.nan)
        for out, src in pit_ratios.items():
            if src in r.columns:
                panels[out][:, j] = pit_align_field(r, dates, src)
        # accruals per book = (earnings - operating cashflow) / book value
        if {"basic_eps", "oper_cf_ps", "net_asset_ps"} <= set(r.columns):
            e = pit_align_field(r, dates, use_of["ep"])
            c = pit_align_field(r, dates, use_of["cfp"])
            b = pit_align_field(r, dates, "net_asset_ps")
            with np.errstate(divide="ignore", invalid="ignore"):
                panels["accruals"][:, j] = np.where(np.abs(b) > 1e-9, (e - c) / b, np.nan)

    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(list(dates)), [str(c) for c in codes]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame({f: panels[f].reshape(-1) for f in feats}, index=idx)
