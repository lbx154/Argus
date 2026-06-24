"""Multi-island evolutionary search for argus (FunSearch / AlphaEvolve style).

K independent search lineages run the unmodified argus engine in parallel, each
in its own cwd (state isolated for free) and seeded toward a different regime
axis. An orchestrator applies periodic migration (share the population-best) and
island-reset (reseed the stalest island into a starved axis). This makes search
diversity STRUCTURAL instead of relying on one agent to jump out of its basin.

See ``orchestrator.IslandOrchestrator`` and ``workspace.setup_island``.
"""
from __future__ import annotations

from .migration import IslandStatus, global_best, read_status, reset_target, starved_axis
from .orchestrator import IslandOrchestrator
from .workspace import DEFAULT_AXES, IslandSpec, setup_island, verify_parity

__all__ = [
    "IslandSpec",
    "setup_island",
    "verify_parity",
    "DEFAULT_AXES",
    "IslandStatus",
    "read_status",
    "global_best",
    "reset_target",
    "starved_axis",
    "IslandOrchestrator",
]
