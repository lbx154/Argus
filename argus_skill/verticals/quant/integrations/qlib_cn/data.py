"""qlib data access for the A-share backtest engine — market-specific binding.

Turns the local qlib ``cn_data_tushare`` dump into the ``(T, S)`` cross-section
the market-agnostic ``factor_toolkit`` consumes, and converts a computed factor
back into the ``(datetime, instrument)`` signal qlib's strategy layer wants.
qlib is imported lazily; :func:`qlib_init` is idempotent so repeated engine runs
share one initialisation.

The dump must exist locally (bin format: calendars / instruments / features).
Default location is ``~/.qlib/qlib_data/cn_data_tushare``; pass ``provider_uri``
to point elsewhere.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import numpy as np

DEFAULT_PROVIDER_URI = os.path.expanduser("~/.qlib/qlib_data/cn_data_tushare")

_QLIB_INITED: dict[str, bool] = {}


def qlib_init(provider_uri: str = DEFAULT_PROVIDER_URI) -> None:
    """Initialise qlib for ``provider_uri`` once per process (idempotent)."""
    if _QLIB_INITED.get(provider_uri):
        return
    try:
        import qlib  # lazy: heavy, not a declared dependency
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "qlib_cn requires qlib — install pyqlib and provide a cn_data dump"
        ) from exc
    qlib.init(provider_uri=provider_uri, region="cn")
    _QLIB_INITED[provider_uri] = True


def list_universe(
    universe: str, start: str, end: str, *, provider_uri: str = DEFAULT_PROVIDER_URI
) -> list[str]:
    """Point-in-time member instruments of ``universe`` (e.g. ``"csi500"``)."""
    qlib_init(provider_uri)
    from qlib.data import D

    return list(
        D.list_instruments(
            D.instruments(universe), start_time=start, end_time=end, as_list=True
        )
    )


def load_qlib_ohlcv(
    universe: str,
    start: str,
    end: str,
    *,
    provider_uri: str = DEFAULT_PROVIDER_URI,
    instruments: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Load an aligned ``(T, S)`` OHLCV panel from the qlib dump.

    Returns a dict with ``open/high/low/close/volume`` as ``(T, S)`` float arrays
    (adjusted; qlib applies its ``$factor``), plus ``dates`` (length T) and
    ``codes`` (length S). ``instruments=None`` uses the ``universe`` membership.
    """
    qlib_init(provider_uri)
    from qlib.data import D

    insts = list(instruments) if instruments is not None else list_universe(
        universe, start, end, provider_uri=provider_uri
    )
    fields = ["$open", "$high", "$low", "$close", "$volume", "$amount"]
    df = D.features(insts, fields, start_time=start, end_time=end)
    if df is None or len(df) == 0:
        raise ValueError(f"qlib returned no features for {universe} {start}..{end}")
    # D.features indexes by (instrument, datetime); unstack instruments to columns.
    inst_level = "instrument" if "instrument" in df.index.names else df.index.names[0]
    date_level = "datetime" if "datetime" in df.index.names else df.index.names[1]
    dates = np.array(sorted(df.index.get_level_values(date_level).unique()))
    codes = tuple(sorted(df.index.get_level_values(inst_level).unique()))
    panel: dict[str, Any] = {"dates": dates, "codes": codes}
    for src, name in zip(fields, ["open", "high", "low", "close", "volume", "amount"]):
        if src not in df.columns:
            continue
        piv = df[src].unstack(inst_level).reindex(index=dates, columns=list(codes))
        panel[name] = piv.to_numpy(dtype=float)
    return panel


def factor_to_signal(factor_values: np.ndarray, dates: Sequence[Any], codes: Sequence[str]):
    """Convert a ``(T, S)`` factor array into a qlib ``(datetime, instrument)`` Series.

    NaNs are dropped. The result is what
    :class:`~.engine.QlibCnEngine`'s strategy scores each rebalance.
    """
    import pandas as pd

    arr = np.asarray(factor_values, dtype=float)
    if arr.shape != (len(dates), len(codes)):
        raise ValueError(
            f"factor shape {arr.shape} != (dates {len(dates)}, codes {len(codes)})"
        )
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(list(dates)), list(codes)], names=["datetime", "instrument"]
    )
    return pd.Series(arr.reshape(-1), index=idx, name="score").dropna()
