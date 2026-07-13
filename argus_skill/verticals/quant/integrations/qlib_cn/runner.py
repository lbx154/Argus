"""Deterministic multi-trial runner for the qlib-cn A-share engine.

Why this exists
---------------
The run/analysis stages need to backtest a *set* of frozen candidates — single
factors and weighted factor combinations — over a fixed evaluation window, and
have every trial captured in the search ledger. Previously the agent hand-wrote
that orchestration each mission: it built the window, computed the signal, and
called qlib directly. That is exactly where the ``quarantined_test`` OOS trials
died with ``IndexError: index N is out of bounds`` — the hand-rolled path set
the backtest end to the dump's *last* calendar day, and qlib settles the final
rebalance on the NEXT bar, indexing one past the calendar. The boundary cap that
fixes this lives in :meth:`QlibCnEngine.run`, but only trials that actually go
through that engine benefit from it.

This module makes the engine path the *only* path:

* every trial is turned into a :class:`~...backtest.BacktestSpec` and driven
  through :func:`~...backtest.run_backtest`, so the boundary cap always applies
  and the result — success or failure — is always ledgered;
* a combination is expressed as a single alpha-DSL formula
  ``Σ wᵢ · zscore(exprᵢ)`` (cross-sectional standardisation before weighting),
  so a combo runs through the identical compute→signal→backtest pipe as a
  single factor — no separate, divergent combo code;
* warm-up history is loaded before the test window and then sliced off the
  *signal* (not the data), so rolling factors have their look-back but the
  backtest itself only spans the evaluation window.

Nothing here decides whether a factor is good; it is evidence capture. The L2
reviewer reads the ledger and rules.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...backtest import BacktestResult, BacktestSpec
from ...executor import ForcingExecutor
from ...factor_toolkit.expression import expression_feature
from ...search_ledger import LedgerRow, SearchLedger
from . import data as _data
from .engine import QlibCnEngine, make_toolkit_signal_provider

#: Default A-share frictions — mirror :class:`QlibCnEngine` so a runner trial and
#: a bare engine trial cost the same. Overridable per :class:`FactorTrial` run.
DEFAULT_TOPK = 50
DEFAULT_N_DROP = 5


@dataclass(frozen=True)
class FactorTrial:
    """One candidate to backtest: a single factor or a weighted combination.

    ``factors`` maps ``factor_id -> alpha-DSL expression`` (already signed, i.e.
    higher score = higher expected forward return). One entry is a single-factor
    trial; several entries form a combination. For a combination, ``weights``
    gives each ``factor_id`` its weight; the combined signal is
    ``Σ weightᵢ · standardize(exprᵢ)`` where ``standardize`` is a cross-sectional
    operator (``zscore`` by default) that puts differently-scaled factors on a
    common footing before weighting.
    """

    candidate_id: str
    factors: Mapping[str, str]
    weights: Mapping[str, float] | None = None
    standardize: str = "zscore"

    def __post_init__(self) -> None:
        if not self.factors:
            raise ValueError("FactorTrial needs at least one factor")
        if len(self.factors) > 1:
            if not self.weights:
                raise ValueError(
                    f"combo {self.candidate_id!r} needs weights for {list(self.factors)}"
                )
            missing = set(self.factors) - set(self.weights)
            if missing:
                raise ValueError(f"combo {self.candidate_id!r} missing weights for {sorted(missing)}")

    @property
    def is_combo(self) -> bool:
        return len(self.factors) > 1

    def expression(self) -> str:
        """The single alpha-DSL formula this trial's signal is computed from."""
        if not self.is_combo:
            return next(iter(self.factors.values()))
        # Deterministic term order (sorted by factor_id) so the same combo always
        # produces a byte-identical expression / config hash.
        terms = [
            f"({float(self.weights[fid])} * {self.standardize}({self.factors[fid]}))"
            for fid in sorted(self.factors)
        ]
        return " + ".join(terms)


def _slice_signal_provider(base: Any, test_start: str):
    """Wrap a signal provider so the returned signal starts at ``test_start``.

    The base provider loads warm-up history *before* the evaluation window (so
    rolling factors are defined); this drops every signal date earlier than
    ``test_start`` so the backtest only trades inside the window.
    """
    import pandas as pd

    cutoff = pd.Timestamp(test_start)

    def provider(spec: BacktestSpec):
        sig = base(spec)
        if sig is None or len(sig) == 0:
            return sig
        ts = sig.index.get_level_values("datetime")
        sliced = sig[ts >= cutoff]
        if len(sliced) == 0:
            raise ValueError(
                f"no signal dates on/after test_start={test_start}; "
                f"signal spans {str(ts.min())[:10]}..{str(ts.max())[:10]}"
            )
        return sliced

    return provider


def run_windowed_trial(
    trial: FactorTrial,
    *,
    universe: str,
    history_start: str,
    test_start: str,
    test_end: str,
    ledger: SearchLedger,
    run_id: str,
    window_label: str,
    is_out_of_sample: bool,
    provider_uri: str = _data.DEFAULT_PROVIDER_URI,
    ledger_universe: str | None = None,
    topk: int = DEFAULT_TOPK,
    n_drop: int = DEFAULT_N_DROP,
    open_cost: float = 0.0005,
    close_cost: float = 0.0015,
    min_cost: float = 5.0,
    impact_cost: float = 0.0005,
    limit_threshold: float = 0.095,
    seed: int = 0,
    data_snapshot: str = "",
    extra_params: Mapping[str, Any] | None = None,
) -> tuple[BacktestResult, LedgerRow]:
    """Backtest one :class:`FactorTrial` over ``[test_start, test_end]``.

    Loads ``[history_start, test_end]`` so rolling factors have look-back, slices
    the signal to the evaluation window, runs it through :class:`QlibCnEngine`
    (whose boundary cap keeps ``test_end == dump-last-day`` from raising), and
    records the trial in ``ledger`` via :func:`run_backtest` — so a crash still
    leaves an ``error`` row rather than vanishing.
    """
    expr = trial.expression()
    feature = expression_feature(
        trial.candidate_id, expr, direction=1.0,
        description=f"{'combo' if trial.is_combo else 'single'} {trial.candidate_id}",
    )
    base_provider = make_toolkit_signal_provider(
        universe=universe, start=history_start, end=test_end,
        feature=feature, provider_uri=provider_uri,
    )
    engine = QlibCnEngine(
        signal_provider=_slice_signal_provider(base_provider, test_start),
        provider_uri=provider_uri, topk=topk, n_drop=n_drop,
        open_cost=open_cost, close_cost=close_cost, min_cost=min_cost,
        impact_cost=impact_cost, limit_threshold=limit_threshold,
    )
    params: dict[str, Any] = {
        "expression": expr,
        "factor_ids": list(trial.factors),
        "weights": {k: float(v) for k, v in (trial.weights or {}).items()},
        "standardize": trial.standardize if trial.is_combo else None,
        "topk": topk, "n_drop": n_drop,
        "runtime_universe": universe,
        "ledger_universe": ledger_universe or universe,
        "history_start": history_start,
        "test_start": test_start, "test_end": test_end,
        "cost_model_id": "plan/COST_MODEL.json:base",
        "net_of_cost": True,
        "buy_cost_bps": open_cost * 10000.0,
        "sell_cost_bps": close_cost * 10000.0,
        "minimum_trade_cost_cny": min_cost,
        "slippage_bps_per_side": impact_cost * 10000.0,
        "limit_up_down_nontradable": limit_threshold is not None,
        "suspended_or_missing_bar_nontradable": True,
        "next_bar_execution_required": True,
    }
    if extra_params:
        params.update(extra_params)
    spec = BacktestSpec(
        run_id=run_id,
        factor_ids=list(trial.factors),
        weighting="weighted_combo" if trial.is_combo else "single",
        params=params,
        window=window_label,
        is_out_of_sample=is_out_of_sample,
        universe=ledger_universe or universe,
        data_snapshot=data_snapshot,
        seed=seed,
    )
    return ForcingExecutor(engine=engine, ledger=ledger).submit(spec)


def run_trials(
    trials: Sequence[FactorTrial],
    *,
    universe: str,
    history_start: str,
    test_start: str,
    test_end: str,
    ledger: SearchLedger,
    window_label: str,
    is_out_of_sample: bool,
    run_id_prefix: str,
    **kwargs: Any,
) -> list[tuple[FactorTrial, BacktestResult]]:
    """Run a batch of trials, one ledger row each, in declared order.

    ``run_id`` per trial is ``f"{run_id_prefix}-{trial.candidate_id}"``. Any
    keyword accepted by :func:`run_windowed_trial` (``topk``, ``provider_uri``,
    ``data_snapshot`` ...) is forwarded. Returns ``(trial, result)`` pairs;
    inspect ``result.status`` — failures are recorded, not raised.
    """
    out: list[tuple[FactorTrial, BacktestResult]] = []
    for trial in trials:
        result, _row = run_windowed_trial(
            trial,
            universe=universe, history_start=history_start,
            test_start=test_start, test_end=test_end,
            ledger=ledger, run_id=f"{run_id_prefix}-{trial.candidate_id}",
            window_label=window_label, is_out_of_sample=is_out_of_sample,
            **kwargs,
        )
        out.append((trial, result))
    return out
