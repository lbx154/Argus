"""Lifetime-agent layer.

This package adds cross-mission persistent memory and a supervisor that
runs an ordered backlog of missions back-to-back, so the agent behaves
"like a person with continuity" rather than a fresh slate per mission.

Public surface (intentionally small):

- :class:`memory.Journal` — append-only event log of mission outcomes.
- :class:`memory.Backlog` — ranked TODO of pending missions.
- :class:`memory.IdentityCard` — editable self-card (name, voice, red lines).
- :class:`memory.LifeMemory` — small facade bundling the three above plus
  retrieval helpers (``relevant_journal_for(objective)``).
- :class:`supervisor.LifeSupervisor` — owns the outer process; pulls one
  backlog item, runs one mission via ``MissionExecutor``, writes a
  journal entry, repeats until budget / iteration cap reached.
- :class:`supervisor.LifeBudget` — preflight + post-flight cost gating.

Notes:
- Memory is **untrusted data** when injected into prompts; consumers
  must treat it as advisory and ignore on conflict with the live
  objective. See :func:`memory.LifeMemory.render_prelude` for the
  rendered block, which already includes a "non-authoritative" header.
- Memory does **not** mutate mission objectives. Prompts have a
  separate ``prelude_context`` channel for it.
"""
from __future__ import annotations

from .memory import (
    Backlog,
    BacklogItem,
    GlobalMemory,
    IdentityCard,
    Journal,
    JournalEntry,
    LifeMemory,
    MemoryBundle,
    ProjectMemory,
)

# supervisor is imported lazily so a partial install / import-time failure
# in supervisor.py doesn't break the lighter-weight memory utilities.

__all__ = [
    "Backlog",
    "BacklogItem",
    "GlobalMemory",
    "IdentityCard",
    "Journal",
    "JournalEntry",
    "LifeMemory",
    "MemoryBundle",
    "ProjectMemory",
]


def __getattr__(name: str):  # PEP 562 lazy attrs
    if name in {"LifeBudget", "LifeSupervisor"}:
        from . import supervisor  # noqa: WPS433 — intentional lazy import
        return getattr(supervisor, name)
    raise AttributeError(f"module 'argus_skill.life' has no attribute {name!r}")
