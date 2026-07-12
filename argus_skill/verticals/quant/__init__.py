"""Quant-factor research vertical — autonomous A-share factor mining.

The finance analog of the ``research`` paper vertical: the same domain-agnostic
harness (planner / engineer / reviewer loop + budget + persistence + gates)
drives an autonomous factor-mining mission whose deliverable is an
interpretable, reviewer-certified **factor report** (the analog of an EMNLP
paper) rather than a numeric speedrun metric.

This package ships two layers:

* the **declarative vertical contract** in :mod:`.stages`
  (``STAGE_ORDER`` / ``STAGE_CHECKS`` / ``REVIEWER_CHECKLISTS`` + checklist
  items, ``role_banner``, ``completion_gate``) — the only thing the harness
  loads via ``argus_skill.verticals._base.load_vertical("quant")``; and
* the **execution-side discipline helpers** the engineer's factor-mining loop
  uses — :mod:`.search_ledger` (hash-chained trial log), :mod:`.backtest` /
  :mod:`.executor` (the ``BacktestExecutor`` contract), :mod:`.factors`,
  :mod:`.leakage_probe`, :mod:`.reference_engine`, and :mod:`.analysis`
  (multiple-testing / orthogonality / OOS discipline), plus the optional, lazily
  imported real A-share backtest engines — :mod:`.integrations.finance_argus`
  (needs the private ``finance_argus`` pkg) and :mod:`.integrations.qlib_cn`
  (qlib + the local ``cn_data_tushare`` dump only; ships the OOS boundary cap,
  the deterministic runner, and — via :mod:`.integrations.adata_cn` — the
  point-in-time fundamental factors the OHLCV dump lacks). See
  ``docs/QUANT_QLIB_CN.md``.

Only :mod:`.stages` is imported on vertical load; the heavy helpers
(numpy/pandas/qlib) are imported only when the engineer actually uses them, so
``load_vertical("quant")`` stays dependency-light and never breaks when the
finance-argus / AShareScreener environment is absent.
"""
from __future__ import annotations

from .stages import (
    CHECKLIST_ITEMS,
    CHECKLIST_STAGE_ORDER,
    REVIEWER_CHECKLISTS,
    STAGE_CHECKS,
    STAGE_ORDER,
    completion_gate,
    role_banner,
)

__all__ = [
    "STAGE_ORDER",
    "STAGE_CHECKS",
    "REVIEWER_CHECKLISTS",
    "CHECKLIST_STAGE_ORDER",
    "CHECKLIST_ITEMS",
    "completion_gate",
    "role_banner",
]
