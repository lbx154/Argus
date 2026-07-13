"""vectorbt integration (skeleton) — single-asset signal backtest engine.

See :mod:`.engine`. Importing this subpackage pulls in only the adapter class,
never ``vectorbt`` itself (that import is deferred to
:meth:`~.engine.VectorbtEngine.run`), so it is safe to import even where
vectorbt is not installed.
"""
from __future__ import annotations

from .engine import SignalProvider, VectorbtEngine

__all__ = ["VectorbtEngine", "SignalProvider"]
