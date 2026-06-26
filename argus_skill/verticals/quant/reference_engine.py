"""Toy reference :class:`BacktestEngine` — numpy-only, no data vendor.

Real factor research uses pandas/qlib/zipline; this engine uses none of them.
Its job is to (a) prove the :mod:`.backtest` adapter contract is sound,
(b) give the e2e tests something to run end-to-end, and (c) give a user a
working example before they wire a production engine.

Mechanics: the engine carries a synthetic panel — ``factors[t, s, f]`` is the
value of factor ``f`` for stock ``s`` on day ``t``; ``returns[t, s]`` is the
forward 1-day return of stock ``s`` over day ``t``. For a trial it:

1. Looks up the requested factor specs from a :class:`FactorRegistry`.
2. Combines them with the requested weighting (``equal_weight`` or
   ``single``) into a per-(t, s) score, applying each spec's ``direction``.
3. Ranks stocks per day, longs the top quintile and shorts the bottom
   quintile, equal-weight inside each leg.
4. Computes daily long-short returns net of a flat per-side transaction cost
   based on portfolio turnover.
5. Reports IC (Spearman of score vs forward return), ICIR (mean / std of
   per-day IC), Sharpe of long-short, turnover, and cost-adjusted return.

The engine is deterministic given a seed. Honest about its toy status — it
does not model T+1, limit-up, capacity, or PIT data; the docstring is the
disclosure.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .backtest import BacktestResult, BacktestSpec
from .factors import FactorRegistry, FactorSpec

_DEFAULT_TX_COST_PER_TURNOVER = 0.0010  # 10 bps each side, applied to turnover


def _rank_pct(row: np.ndarray) -> np.ndarray:
    """Per-row percentile rank in [0, 1]; NaNs propagate as NaN."""
    out = np.full_like(row, np.nan, dtype=float)
    mask = ~np.isnan(row)
    if mask.sum() < 2:
        return out
    valid = row[mask]
    order = valid.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(valid), dtype=float)
    out[mask] = ranks / max(len(valid) - 1, 1)
    return out


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rank correlation. NaNs dropped pairwise; <3 pairs → NaN."""
    mask = ~(np.isnan(x) | np.isnan(y))
    if mask.sum() < 3:
        return float("nan")
    xr = _rank_pct(x[mask])
    yr = _rank_pct(y[mask])
    if np.std(xr) == 0 or np.std(yr) == 0:
        return float("nan")
    return float(np.corrcoef(xr, yr)[0, 1])


@dataclass
class ToyPanel:
    """Synthetic ``(T, S, F)`` factor cube + ``(T, S)`` forward returns."""

    factor_values: np.ndarray  # shape (T, S, F)
    forward_returns: np.ndarray  # shape (T, S)
    factor_order: tuple[str, ...]  # length F; aligns with last axis above
    universe: str = "toy"
    data_snapshot: str = "toy:v1"

    def __post_init__(self) -> None:
        if self.factor_values.ndim != 3:
            raise ValueError("factor_values must be a 3D (T, S, F) array")
        if self.forward_returns.ndim != 2:
            raise ValueError("forward_returns must be a 2D (T, S) array")
        T, S, F = self.factor_values.shape
        if self.forward_returns.shape != (T, S):
            raise ValueError("forward_returns shape must match (T, S)")
        if len(self.factor_order) != F:
            raise ValueError("factor_order length must equal F")


def make_synthetic_panel(
    *,
    n_days: int = 60,
    n_stocks: int = 30,
    factor_specs: tuple[FactorSpec, ...],
    seed: int = 7,
    signal_strength: float = 0.15,
) -> ToyPanel:
    """Build a deterministic synthetic panel.

    The first factor in ``factor_specs`` carries a real signal of strength
    ``signal_strength`` (forward return correlates with it after applying its
    declared ``direction``). Remaining factors are independent noise. This
    makes the engine's IC numbers meaningful in tests without baking finance
    domain knowledge in.
    """
    if not factor_specs:
        raise ValueError("factor_specs must be non-empty")
    rng = np.random.default_rng(seed)
    T, S, F = n_days, n_stocks, len(factor_specs)
    factor_values = rng.standard_normal((T, S, F))
    noise = rng.standard_normal((T, S))
    direction = factor_specs[0].direction
    forward_returns = (
        signal_strength * direction * factor_values[:, :, 0] + noise
    ) * 0.01
    return ToyPanel(
        factor_values=factor_values,
        forward_returns=forward_returns,
        factor_order=tuple(spec.factor_id for spec in factor_specs),
    )


@dataclass
class ToyBacktestEngine:
    """Reference :class:`BacktestEngine` over a :class:`ToyPanel`.

    ``name`` is the provenance string the search ledger records; tests assert
    against it.
    """

    panel: ToyPanel
    registry: FactorRegistry
    name: str = "toy-numpy@v1"
    tx_cost_per_turnover: float = _DEFAULT_TX_COST_PER_TURNOVER

    def _config_hash(self, spec: BacktestSpec) -> str:
        h = hashlib.sha256()
        h.update(self.name.encode())
        h.update(self.panel.data_snapshot.encode())
        for fid in spec.factor_ids:
            h.update(fid.encode())
        h.update(spec.weighting.encode())
        return h.hexdigest()[:16]

    def _resolve_factor_columns(
        self, factor_ids: tuple[str, ...]
    ) -> tuple[np.ndarray, tuple[float, ...]]:
        """Return the (T, S, k) sub-cube and per-factor direction signs."""
        idx_lookup = {fid: i for i, fid in enumerate(self.panel.factor_order)}
        cols: list[int] = []
        signs: list[float] = []
        for fid in factor_ids:
            spec = self.registry.get(fid)  # raises KeyError if unknown
            if fid not in idx_lookup:
                raise KeyError(
                    f"factor_id {fid!r} not in panel; "
                    f"panel carries {self.panel.factor_order}"
                )
            cols.append(idx_lookup[fid])
            signs.append(spec.direction)
        sub = self.panel.factor_values[:, :, cols]
        return sub, tuple(signs)

    def _combine(
        self,
        sub: np.ndarray,
        signs: tuple[float, ...],
        weighting: str,
    ) -> np.ndarray:
        """Combine k factors into a (T, S) score with per-factor direction."""
        T, S, k = sub.shape
        directed = sub * np.array(signs).reshape(1, 1, k)
        # Cross-sectional rank per day per factor before combining, so units
        # do not need to match.
        ranks = np.empty_like(directed)
        for t in range(T):
            for f in range(k):
                ranks[t, :, f] = _rank_pct(directed[t, :, f])
        if weighting == "single":
            if k != 1:
                raise ValueError("weighting='single' requires exactly one factor")
            return ranks[:, :, 0]
        if weighting == "equal_weight":
            return np.nanmean(ranks, axis=2)
        raise ValueError(f"unsupported weighting {weighting!r}")

    def _portfolio(self, score: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Long-short top/bottom-quintile weights from per-day score."""
        T, S = score.shape
        weights = np.zeros_like(score)
        q_size = max(S // 5, 1)
        for t in range(T):
            row = score[t]
            mask = ~np.isnan(row)
            if mask.sum() < 2 * q_size:
                continue
            order = np.argsort(np.where(mask, row, -np.inf))
            longs = order[-q_size:]
            shorts = order[:q_size]
            weights[t, longs] = 1.0 / q_size
            weights[t, shorts] = -1.0 / q_size
        # Daily turnover = 0.5 * L1(diff). First day is full turnover from cash.
        turnover = np.empty(T)
        turnover[0] = float(np.abs(weights[0]).sum()) * 0.5
        for t in range(1, T):
            turnover[t] = float(np.abs(weights[t] - weights[t - 1]).sum()) * 0.5
        return weights, turnover

    def run(self, spec: BacktestSpec) -> BacktestResult:
        sub, signs = self._resolve_factor_columns(tuple(spec.factor_ids))
        score = self._combine(sub, signs, spec.weighting or "equal_weight")
        fwd = self.panel.forward_returns

        # Per-day IC: Spearman(score, forward return)
        T = score.shape[0]
        per_day_ic = np.array(
            [_spearman(score[t], fwd[t]) for t in range(T)], dtype=float
        )
        ic = float(np.nanmean(per_day_ic))
        ic_std = float(np.nanstd(per_day_ic))
        icir = ic / ic_std if ic_std > 0 else float("nan")

        weights, turnover = self._portfolio(score)
        gross_pnl = np.nansum(weights * fwd, axis=1)
        cost = self.tx_cost_per_turnover * turnover
        net_pnl = gross_pnl - cost

        sharpe = (
            float(np.mean(net_pnl) / np.std(net_pnl) * np.sqrt(252))
            if np.std(net_pnl) > 0
            else float("nan")
        )
        cumulative = float(np.prod(1.0 + net_pnl) - 1.0)
        avg_turnover = float(np.mean(turnover))

        return BacktestResult(
            run_id=spec.run_id,
            status="ok",
            metrics={
                "ic": ic,
                "icir": icir,
                "sharpe": sharpe,
                "cumulative_return": cumulative,
                "turnover": avg_turnover,
                "long_short_mean": float(np.mean(net_pnl)),
                "cost_drag": float(np.mean(cost)),
            },
            engine=self.name,
            config_hash=self._config_hash(spec),
        )
