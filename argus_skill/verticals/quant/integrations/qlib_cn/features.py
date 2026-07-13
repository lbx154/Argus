"""Model feature matrices for the qlib-cn path: Alpha360 [+ PIT fundamentals].

The single-factor path (``runner.py``) screens one alpha at a time; a model path
instead learns a non-linear cross-sectional combination of a whole factor library.
This module assembles that library:

* :func:`load_alpha360` — qlib's canonical 360-feature technical handler, computed
  straight off the local ``cn_data_tushare`` dump (raw features; the model is a
  GBDT so it is scale-robust — no normalisation processors needed);
* :func:`build_feature_matrix` — Alpha360 optionally joined 1:1 with the PIT
  fundamental features (``fundamental_feature_frame``: EP/BP/CFP + growth), on the
  shared ``(datetime, instrument)`` index, plus the forward-return label;
* :func:`time_split` — time-ordered train / valid / test masks (no shuffling, so
  the test window is a genuine out-of-sample future).

Fundamentals are the *cross-family* leg the doc's §11/§12 argue the technical-only
Alpha zoo lacks; this is where they enter the model.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from . import data as _data


def cross_sectional_normalize(X: Any, *, method: str = "rank") -> Any:
    """Per-day cross-sectional normalisation of every feature column.

    ``"rank"`` → per-date percentile rank centred to ``[-0.5, 0.5]`` (robust to
    outliers/scale; the standard transform for cross-sectional prediction);
    ``"zscore"`` → per-date ``(x-mean)/std``. NaNs are preserved. This is the
    cheap lever that stops a model fighting scale/outliers instead of learning the
    daily cross-sectional ordering.
    """
    g = X.groupby(level="datetime")
    if method == "rank":
        return g.rank(pct=True) - 0.5
    if method == "zscore":
        import pandas as pd  # noqa: F401

        mu = g.transform("mean")
        sd = g.transform("std")
        return (X - mu) / sd.replace(0, np.nan)
    raise ValueError(f"unknown normalize method {method!r} (use 'rank' or 'zscore')")


def forward_return_label(
    universe: str, start: str, end: str, horizon: int, *, provider_uri: str = _data.DEFAULT_PROVIDER_URI
) -> Any:
    """``horizon``-day forward return per ``(datetime, instrument)`` from qlib close.

    ``label(t) = close(t+horizon)/close(t) - 1`` — the target a model should predict
    to trade a ``horizon``-day hold. Longer horizons carry more signal-to-noise than
    the ~1-day Alpha360 default (microstructure noise dominates intraday/next-day).
    """
    import pandas as pd

    panel = _data.load_qlib_ohlcv(universe, start, end, provider_uri=provider_uri)
    close = np.asarray(panel["close"], dtype=float)
    T = close.shape[0]
    fwd = np.full_like(close, np.nan)
    if T > horizon:
        with np.errstate(divide="ignore", invalid="ignore"):
            fwd[: T - horizon] = close[horizon:] / close[: T - horizon] - 1.0
    idx = pd.MultiIndex.from_product(
        [pd.to_datetime(list(panel["dates"])), [str(c) for c in panel["codes"]]],
        names=["datetime", "instrument"],
    )
    return pd.Series(fwd.reshape(-1), index=idx, name="label")


def load_alpha360(
    universe: str, start: str, end: str, *, provider_uri: str = _data.DEFAULT_PROVIDER_URI
):
    """qlib Alpha360 ``(feature_df, label_series)`` over the dump.

    ``feature_df`` is a ``(datetime, instrument)`` MultiIndexed frame of 360 raw
    technical features; ``label_series`` is Alpha360's default forward-return
    label (``Ref($close,-2)/Ref($close,-1)-1``). Raw (no processors) — the GBDT
    handles scale and NaNs natively.
    """
    _data.qlib_init(provider_uri)
    from qlib.contrib.data.handler import Alpha360

    h = Alpha360(
        instruments=universe, start_time=start, end_time=end,
        infer_processors=[], learn_processors=[],
        fit_start_time=start, fit_end_time=end,
    )
    feat = h.fetch(col_set="feature")
    label = h.fetch(col_set="label")
    return feat, label.iloc[:, 0]


def build_feature_matrix(
    universe: str,
    start: str,
    end: str,
    *,
    with_fundamentals: bool = False,
    fetch: Any = None,
    ttm: bool = True,
    normalize: str | None = None,
    label_horizon: int | None = None,
    provider_uri: str = _data.DEFAULT_PROVIDER_URI,
):
    """Assemble ``(X, y)`` for the model over ``[start, end]``.

    ``X`` = Alpha360 (columns ``a360_*``) left-joined with the PIT fundamental
    features (columns ``fund_*``) when ``with_fundamentals``. ``normalize`` applies
    a per-day cross-sectional transform (``"rank"``/``"zscore"``) to every feature.
    ``y`` = Alpha360's ~1-day label, or a ``label_horizon``-day forward return when
    given. Features are float32; NaNs preserved (lightgbm-native).
    """
    import pandas as pd  # noqa: F401  (kept for parity / future use)

    feat, label = load_alpha360(universe, start, end, provider_uri=provider_uri)
    X = feat.copy()
    X.columns = [f"a360_{c}" for c in X.columns]
    if with_fundamentals:
        from ..adata_cn.fundamentals import fundamental_feature_frame

        panel = _data.load_qlib_ohlcv(universe, start, end, provider_uri=provider_uri)
        fund = fundamental_feature_frame(
            panel["codes"], panel["dates"], panel["close"], fetch=fetch, ttm=ttm
        )
        fund = fund.reindex(X.index)  # 1:1 align to Alpha360 rows
        fund.columns = [f"fund_{c}" for c in fund.columns]
        X = X.join(fund, how="left")
    if normalize:
        X = cross_sectional_normalize(X, method=normalize)
    X = X.astype("float32")
    if label_horizon:
        y = forward_return_label(
            universe, start, end, label_horizon, provider_uri=provider_uri
        ).reindex(X.index)
    else:
        y = label.reindex(X.index)
    y.name = "label"
    return X, y


def time_split(index: Any, *, train: tuple[str, str], valid: tuple[str, str], test: tuple[str, str]):
    """Boolean masks over a ``(datetime, instrument)`` index for 3 date ranges.

    Ranges are ``(start, end)`` inclusive; they must be time-ordered
    (train < valid < test) for the test split to be a real out-of-sample future.
    """
    import pandas as pd

    dt = index.get_level_values("datetime")

    def mask(rng: tuple[str, str]):
        return (dt >= pd.Timestamp(rng[0])) & (dt <= pd.Timestamp(rng[1]))

    return {"train": mask(train), "valid": mask(valid), "test": mask(test)}
