"""finance-argus integration for the quant-factor domain.

Plugs the real A-share engine from the ``finance_argus`` package (real
tushare/adata data, IC-weighted factor combination, and the qlib production
backtest with realistic A-share costs) into the quant-factor domain's
``BacktestEngine`` / ``FactorRegistry`` Protocols.

Quick start (CI / mock — no tushare or qlib needed)::

    from finance_argus.core.loop import mock_backtest
    from argus_skill.domains.quant_factor.integrations.finance_argus import (
        build_finance_argus_registry, make_finance_argus_executor,
    )

    registry = build_finance_argus_registry()            # the 9 A-share factors
    executor = make_finance_argus_executor(
        ledger_path="run/SEARCH_LEDGER.jsonl",
        backtest_fn=mock_backtest, backtest_fn_kind="mock",
        recommendations_dir="run_out",
    )

Real qlib path (default ``backtest_fn=None``) needs the finance-argus package
installed (``pip install -e ../AShareScreener`` and ``.[qlib]``), a
``TINYSHARE_TOKEN``, and a one-time qlib data dump. ``finance_argus`` is
imported lazily, so importing this subpackage never pulls in pandas/qlib.
"""
from __future__ import annotations

from .engine import FinanceArgusEngine, map_metrics
from .factory import make_finance_argus_executor
from .provenance import compute_config_hash, resolve_data_snapshot
from .qlib_runner import qlib_backtest_run
from .recommendations import JsonRecommendationSink, declared_weights
from .registry import build_finance_argus_registry, factor_spec_from_definition
from .windows import WindowSchedule

__all__ = [
    "FinanceArgusEngine",
    "map_metrics",
    "make_finance_argus_executor",
    "build_finance_argus_registry",
    "factor_spec_from_definition",
    "qlib_backtest_run",
    "WindowSchedule",
    "JsonRecommendationSink",
    "declared_weights",
    "compute_config_hash",
    "resolve_data_snapshot",
]
