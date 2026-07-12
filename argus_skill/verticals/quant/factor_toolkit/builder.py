"""Bridge: computed features → argus ``FactorSpec`` panel + registry.

This is the piece the quant vertical was missing — a *factor-generation* layer.
:func:`build_feature_panel` takes a raw OHLCV cross-section (each field a
``(T, S)`` array over ``T`` bars and ``S`` instruments), applies a set of
market-agnostic feature functions (see :mod:`.price_features`,
:mod:`.volatility`), and returns:

* a :class:`~..reference_engine.ToyPanel` — the ``(T, S, F)`` factor cube plus
  the aligned forward returns — that the existing
  :class:`~..reference_engine.ToyBacktestEngine` can backtest immediately, and
* an :class:`~..factors.InMemoryFactorRegistry` of :class:`~..factors.FactorSpec`
  whose ``factor_id`` s match the cube's ``factor_order``.

So a mining loop can go: build panel → hand ``(engine, ledger)`` to a
``ForcingExecutor`` → run cross-sectional trials → land search-ledger rows —
with real computed factors instead of synthetic noise.

The RAW OHLCV is supplied by the caller: for A-share it comes from the
``finance_argus`` integration, for futures / crypto from their own
``integrations/<market>/`` loaders. The feature MATH here is market-agnostic;
only the data binding is per-market.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ..factors import FactorSpec, InMemoryFactorRegistry
from ..reference_engine import ToyPanel
from . import price_features, volatility

#: Raw OHLCV cross-section: field name -> ``(T, S)`` array. ``close`` required.
OHLCVPanel = Mapping[str, np.ndarray]


@dataclass(frozen=True)
class FeatureSpec:
    """One factor to compute from a raw OHLCV panel.

    ``compute`` maps the OHLCV panel to a ``(T, S)`` feature array. ``direction``
    (+1/-1), ``transform``, ``neutralize`` and ``description`` become the
    resulting :class:`~..factors.FactorSpec` fields — ``description`` must state
    the economic thesis (an empty one is a review red flag).
    """

    name: str
    compute: Callable[[OHLCVPanel], np.ndarray]
    direction: float = 1.0
    transform: str = "rank"
    neutralize: bool = True
    description: str = ""


def default_feature_catalog(
    *,
    momentum_windows: Sequence[int] = (20, 60),
    reversal_window: int = 5,
    vol_window: int = 20,
) -> list[FeatureSpec]:
    """A small, diverse starter catalog spanning trend / reversal / risk.

    Each factor carries a stated economic mechanism and an expected sign, so the
    panel it builds is review-ready rather than an unlabelled feature dump.
    """
    catalog: list[FeatureSpec] = []
    for w in momentum_windows:
        catalog.append(
            FeatureSpec(
                name=f"momentum_{w}",
                compute=(lambda o, w=w: price_features.momentum(o["close"], w)),
                direction=1.0,
                description=(
                    f"{w}-bar price momentum; trailing winners continue to "
                    "outperform (momentum premium)."
                ),
            )
        )
    catalog.append(
        FeatureSpec(
            name=f"reversal_{reversal_window}",
            compute=(lambda o: price_features.reversal(o["close"], reversal_window)),
            direction=1.0,
            description=(
                f"{reversal_window}-bar short-horizon reversal (negated recent "
                "return); recent losers rebound (overreaction correction)."
            ),
        )
    )
    catalog.append(
        FeatureSpec(
            name=f"low_vol_{vol_window}",
            compute=(
                lambda o: volatility.realized_vol(o["close"], window=vol_window)
            ),
            direction=-1.0,
            description=(
                f"{vol_window}-bar realized volatility; the low-volatility "
                "anomaly — lower-vol names earn higher risk-adjusted returns "
                "(direction -1)."
            ),
        )
    )
    return catalog


def _to_spec(f: FeatureSpec) -> FactorSpec:
    return FactorSpec(
        factor_id=f.name,
        source=f.name,
        direction=f.direction,
        transform=f.transform,
        neutralize=f.neutralize,
        description=f.description or f"feature {f.name!r}",
    )


def build_feature_panel(
    ohlcv: OHLCVPanel,
    forward_returns: np.ndarray,
    *,
    features: Sequence[FeatureSpec] | None = None,
    universe: str = "toolkit",
    data_snapshot: str = "toolkit:v1",
) -> tuple[ToyPanel, InMemoryFactorRegistry]:
    """Compute ``features`` over ``ohlcv`` and package them for the toy engine.

    Parameters
    ----------
    ohlcv
        Field -> ``(T, S)`` array; must contain ``"close"`` and whatever fields
        the chosen features read (e.g. ``"high"``/``"low"``/``"open"``).
    forward_returns
        ``(T, S)`` forward returns aligned to the SAME bars as ``ohlcv`` — the
        target the engine scores each factor against. The caller is responsible
        for the no-look-ahead alignment (feature at ``t`` vs return over
        ``t -> t+h``); this bridge does not shift for you.
    features
        Feature list; ``None`` uses :func:`default_feature_catalog`.

    Returns ``(ToyPanel, InMemoryFactorRegistry)`` ready for
    :class:`~..reference_engine.ToyBacktestEngine`. Raises ``ValueError`` on
    shape mismatch or a missing required OHLCV field.
    """
    if "close" not in ohlcv:
        raise ValueError("ohlcv must contain a 'close' field")
    close = np.asarray(ohlcv["close"], dtype=float)
    if close.ndim != 2:
        raise ValueError("OHLCV fields must be 2-D (T, S) arrays")
    fwd = np.asarray(forward_returns, dtype=float)
    if fwd.shape != close.shape:
        raise ValueError(
            f"forward_returns {fwd.shape} must match close {close.shape}"
        )

    specs = list(features) if features is not None else default_feature_catalog()
    if not specs:
        raise ValueError("no features to build")
    seen: set[str] = set()
    columns: list[np.ndarray] = []
    for f in specs:
        if f.name in seen:
            raise ValueError(f"duplicate feature name {f.name!r}")
        seen.add(f.name)
        col = np.asarray(f.compute(ohlcv), dtype=float)
        if col.shape != close.shape:
            raise ValueError(
                f"feature {f.name!r} produced shape {col.shape}, expected {close.shape}"
            )
        columns.append(col)

    cube = np.stack(columns, axis=2)  # (T, S, F)
    panel = ToyPanel(
        factor_values=cube,
        forward_returns=fwd,
        factor_order=tuple(f.name for f in specs),
        universe=universe,
        data_snapshot=data_snapshot,
    )
    registry = InMemoryFactorRegistry.from_iter(_to_spec(f) for f in specs)
    return panel, registry
