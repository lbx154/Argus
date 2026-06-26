"""Vendor integrations for the quant-factor domain.

Each subpackage adapts a concrete, external factor library + backtest engine
onto the quant-factor Protocols (``BacktestEngine``, ``FactorRegistry``). The
adapters live here — not in the domain core — so the core never grows a hard
dependency on a data vendor (tushare/qlib/pandas). Import a subpackage only
when you intend to use that vendor.
"""
