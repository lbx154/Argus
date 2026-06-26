"""Forced-ledger executor port: the only sanctioned way to run a backtest.

The :class:`~.backtest.BacktestEngine` adapter is a Protocol — anyone with a
reference to it can call ``engine.run(spec)`` directly and never write the
trial to the search ledger. That defeats the purpose of the ledger as the
audit substrate the L2 reviewer reads to judge search breadth.

This module closes the gap. The supervisor / engineer harness should hand
the engineer a :class:`BacktestExecutor` (Protocol), not a raw engine; the
canonical implementation, :class:`ForcingExecutor`, funnels every call
through :func:`~.backtest.run_backtest` so a row is appended *before* the
caller sees the result. There is no engineer-visible code path that can
write a trial outside the ledger.

Concurrency: factor screens often fan out trials in parallel; the underlying
:class:`~.search_ledger.SearchLedger` is documented as single-writer.
:class:`ForcingExecutor` serialises ``run`` with a lock so parallel callers
share one ledger safely. (For multi-process parallelism users should
shard ledgers and merge offline; in-process is what the harness exercises.)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .backtest import BacktestEngine, BacktestResult, BacktestSpec, run_backtest
from .search_ledger import LedgerRow, SearchLedger


@runtime_checkable
class BacktestExecutor(Protocol):
    """The single execution surface the engineer is given.

    A caller cannot reach the underlying engine from this Protocol. Every
    invocation results in a ledger row by construction.
    """

    def submit(self, spec: BacktestSpec) -> tuple[BacktestResult, LedgerRow]:
        ...


@dataclass
class ForcingExecutor:
    """Reference :class:`BacktestExecutor` that funnels through the ledger.

    Holds private references to the engine and ledger; exposes only
    :meth:`submit`. A lock serialises writes so concurrent submitters share
    one ledger without breaking the hash chain.

    Invariant: a successful return from :meth:`submit` implies a ledger row
    was fsynced. A raised exception inside the engine becomes a
    ``status="error"`` :class:`~.backtest.BacktestResult` and is *still*
    appended (so a failing trial cannot vanish from the search breadth).
    """

    engine: BacktestEngine
    ledger: SearchLedger
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Sanity: the engine must satisfy the runtime-checkable Protocol.
        if not isinstance(self.engine, BacktestEngine):
            raise TypeError(
                f"engine {self.engine!r} does not implement BacktestEngine "
                f"(missing 'name' or 'run'?)"
            )
        # A fresh, unshared lock per executor — the ledger is the shared
        # resource we serialise on.
        object.__setattr__(self, "_lock", threading.Lock())

    def submit(self, spec: BacktestSpec) -> tuple[BacktestResult, LedgerRow]:
        with self._lock:
            return run_backtest(self.engine, spec, self.ledger)

    @property
    def trials_recorded(self) -> int:
        """Convenience: the number of rows in the ledger.

        Useful inside the analysis stage's multiple-testing accounting, where
        the haircut is a function of how many trials were actually run.
        """
        return len(self.ledger)
