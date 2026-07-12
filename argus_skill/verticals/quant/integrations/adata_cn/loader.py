"""adata A-share market-data integration — real OHLCV panels for the toolkit.

Wraps the ``adata`` library (a free, multi-source A-share data SDK) as the
market-data loader that feeds :func:`...factor_toolkit.build_feature_panel`.
This is the concrete A-share binding the vertical needed: the feature MATH stays
market-agnostic in ``factor_toolkit`` / ``analysis``; only this loader knows
about A-share codes, adjusted prices, and the trading calendar.

Key surface:

* :func:`load_ohlcv_panel` — fetch adjusted daily OHLCV for a list of codes and
  pivot into the ``(T, S)`` cross-section arrays the toolkit consumes.
* :func:`forward_returns` — next-``horizon`` return aligned to each bar (the
  no-look-ahead target for factor scoring).
* :func:`to_feature_panel` — one call: codes -> panel -> ``ToyPanel`` + registry,
  runnable through the ForcingExecutor and search ledger.

``adata`` is imported lazily (it is an on-line fetcher, not a declared
dependency, and needs network access); it is injectable via the ``fetch``
argument so the assembly logic is unit-testable offline with a fake source.

adata: https://github.com/1nchaos/adata  (专注A股行情数据).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

# adata get_market column -> our field name. adata returns adjusted OHLCV plus
# amount and turnover_ratio (the latter feeds the liquidity/turnover factor).
_FIELD_SOURCE: dict[str, str] = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
    "turnover": "turnover_ratio",
}

#: A fetcher maps (code, start_date, end_date, k_type, adjust_type) -> a per-code
#: DataFrame with at least trade_date + the OHLCV columns above. The default hits
#: adata; tests inject a synthetic one.
Fetcher = Callable[[str, str, str | None, int, int], Any]


def _default_fetch(
    code: str, start_date: str, end_date: str | None, k_type: int, adjust_type: int
) -> Any:
    """Fetch one code's k-line via adata (lazy import; network required)."""
    try:
        import adata  # lazy: on-line fetcher, not a declared dependency
    except ImportError as exc:  # pragma: no cover - exercised only when absent
        raise ImportError(
            "adata_cn requires the 'adata' package — install it with "
            "`pip install adata` to load real A-share data"
        ) from exc
    return adata.stock.market.get_market(
        stock_code=code, start_date=start_date, end_date=end_date,
        k_type=k_type, adjust_type=adjust_type,
    )


def load_ohlcv_panel(
    codes: Sequence[str],
    *,
    start_date: str,
    end_date: str | None = None,
    adjust_type: int = 1,
    k_type: int = 1,
    fields: Sequence[str] = ("open", "high", "low", "close", "volume", "turnover"),
    fetch: Fetcher | None = None,
) -> dict[str, Any]:
    """Load an aligned ``(T, S)`` OHLCV cross-section for ``codes``.

    Parameters
    ----------
    codes
        A-share codes (e.g. ``["000001", "600519"]``).
    start_date / end_date
        ``"YYYY-MM-DD"`` bounds; ``end_date=None`` means up to the latest bar.
    adjust_type
        adata adjust mode: ``1`` = 前复权 (default, correct for factor research),
        ``2`` = 后复权, ``0`` = raw. Point-in-time hygiene is the caller's job.
    k_type
        adata k-line type: ``1`` daily (default), ``2`` weekly, ``3`` monthly.
    fields
        Which panels to build (keys of the returned dict).

    Returns a dict with each requested field as a ``(T, S)`` float array (NaN
    where a stock has no bar on a date), plus ``dates`` (sorted unique trade
    dates, length T) and ``codes`` (the codes actually returned, length S).
    Codes that returned no data are dropped. Raises ``ValueError`` if nothing
    was fetched.
    """
    import pandas as pd  # lazy; only the real path needs pandas

    fetch = fetch or _default_fetch
    frames: list[Any] = []
    for code in codes:
        df = fetch(code, start_date, end_date, k_type, adjust_type)
        if df is None or len(df) == 0:
            continue
        df = df.copy()
        df["__code"] = str(code)
        frames.append(df)
    if not frames:
        raise ValueError("adata returned no data for the requested codes/date range")

    big = pd.concat(frames, ignore_index=True)
    big["trade_date"] = pd.to_datetime(big["trade_date"])
    dates = np.array(sorted(big["trade_date"].unique()))
    present = set(big["__code"])
    code_list = [str(c) for c in codes if str(c) in present]

    panel: dict[str, Any] = {"dates": dates, "codes": tuple(code_list)}
    for field in fields:
        src = _FIELD_SOURCE.get(field)
        if src is None or src not in big.columns:
            continue
        piv = big.pivot_table(index="trade_date", columns="__code", values=src, aggfunc="last")
        piv = piv.reindex(index=dates, columns=code_list)
        panel[field] = piv.to_numpy(dtype=float)
    return panel


def forward_returns(close: np.ndarray, *, horizon: int = 1) -> np.ndarray:
    """Next-``horizon`` return aligned to each bar: ``close[t+h]/close[t] - 1``.

    The factor at ``t`` is scored against this forward return; the last
    ``horizon`` rows are NaN (no future bar). No look-ahead by construction.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    c = np.asarray(close, dtype=float)
    out = np.full_like(c, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:-horizon] = c[horizon:] / c[:-horizon] - 1.0
    return out


def to_feature_panel(
    panel: dict[str, Any],
    *,
    features: Sequence[Any] | None = None,
    horizon: int = 1,
):
    """Turn a loaded OHLCV ``panel`` into a ``(ToyPanel, registry)`` pair.

    Computes forward returns from ``panel["close"]`` and hands the OHLCV +
    returns to :func:`...factor_toolkit.build_feature_panel`, so the result runs
    straight through ``ToyBacktestEngine`` / ``ForcingExecutor`` into the search
    ledger. ``features`` defaults to the toolkit's starter catalog.
    """
    from ...factor_toolkit import build_feature_panel

    ohlcv = {
        k: panel[k] for k in ("open", "high", "low", "close", "volume") if k in panel
    }
    if "close" not in ohlcv:
        raise ValueError("panel must contain 'close' to build a feature panel")
    fwd = forward_returns(panel["close"], horizon=horizon)
    dates = panel.get("dates")
    snapshot = "adata:cn"
    if dates is not None and len(dates):
        snapshot = f"adata:cn@{str(dates[0])[:10]}..{str(dates[-1])[:10]}"
    return build_feature_panel(
        ohlcv, fwd, features=features, universe="adata_cn", data_snapshot=snapshot
    )


def all_a_codes(fetch_codes: Callable[[], Any] | None = None) -> list[str]:
    """Return all A-share codes (the investable universe) via adata.

    Injectable for tests. Falls back to ``adata.stock.info.all_code()`` and
    reads its ``stock_code`` column.
    """
    if fetch_codes is not None:
        df = fetch_codes()
    else:
        try:
            import adata  # lazy
        except ImportError as exc:  # pragma: no cover
            raise ImportError("adata_cn.all_a_codes requires the 'adata' package") from exc
        df = adata.stock.info.all_code()
    return [str(c) for c in df["stock_code"].tolist()]
