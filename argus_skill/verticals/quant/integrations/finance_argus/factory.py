"""Factory: assemble a ledger-forcing executor backed by finance-argus.

One call wires the real engine, the search ledger, and (optionally) the
recommendation sink into a :class:`~...executor.ForcingExecutor` — the only
sanctioned surface the engineer uses to run trials. Every ``submit`` lands a
hash-chained ledger row by construction.

Default is the real qlib path. Pass ``backtest_fn=mock_backtest,
backtest_fn_kind="mock"`` for CI runs that need no tushare/qlib.
"""
from __future__ import annotations

import os

from ...executor import ForcingExecutor
from ...search_ledger import SearchLedger
from .engine import BacktestFn, BacktestFnKind, FinanceArgusEngine
from .recommendations import JsonRecommendationSink
from .windows import WindowSchedule


def make_finance_argus_executor(
    ledger_path: str | os.PathLike[str],
    *,
    engine_name: str = "finance-argus-qlib@v1",
    schedule: WindowSchedule | None = None,
    universe: str = "csi300",
    topk: int = 50,
    n_drop: int = 5,
    benchmark: str | None = None,
    backtest_fn: BacktestFn | None = None,
    backtest_fn_kind: BacktestFnKind | None = None,
    provider_uri: str | None = None,
    data_snapshot_override: str | None = None,
    recommendations_dir: str | os.PathLike[str] | None = None,
) -> ForcingExecutor:
    """Build a :class:`ForcingExecutor` wrapping a :class:`FinanceArgusEngine`.

    ``ledger_path`` is the JSONL search ledger (e.g. ``<run>/SEARCH_LEDGER.jsonl``).
    ``recommendations_dir`` enables the picks / combination sidecar artefacts
    (``run/COMBINATIONS.json`` + ``recommendations/<run_id>.json``) under that
    directory; ``None`` disables them.
    """
    engine = FinanceArgusEngine(
        name=engine_name,
        schedule=schedule or WindowSchedule(),
        universe_default=universe,
        topk=topk,
        n_drop=n_drop,
        benchmark=benchmark,
        backtest_fn=backtest_fn,
        backtest_fn_kind=backtest_fn_kind,
        provider_uri=provider_uri,
        data_snapshot_override=data_snapshot_override,
        recorder=JsonRecommendationSink(recommendations_dir)
        if recommendations_dir is not None
        else None,
    )
    ledger = SearchLedger(ledger_path)
    return ForcingExecutor(engine=engine, ledger=ledger)
