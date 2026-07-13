"""backtrader integration (skeleton) — single-asset event-driven engine.

See :mod:`.engine`. Importing this subpackage pulls in only the adapter class,
never ``backtrader`` itself (deferred to :meth:`~.engine.BacktraderEngine.run`),
so it is safe to import even where backtrader is not installed.
"""
from __future__ import annotations

from .engine import BacktraderEngine, CerebroBuilder

__all__ = ["BacktraderEngine", "CerebroBuilder"]
