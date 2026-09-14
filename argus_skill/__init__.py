"""argus-skill: the persistent, reviewed runtime behind the ``argus-skill`` CLI.

Argus drains a Project's backlog forever through four persistent roles: the
Manager routes operator input and advances the pipeline stage, the Planner
turns an objective into concrete tasks, the Engineer executes rounds against a
real agent CLI, and the Reviewer alone decides done / continue / blocked. Each
mission is one ``SkillLoop`` run; the Skill library, wiki and event ledger
carry memory across missions.

Layer: delivery

Public names (``__all__``):

- ``LoopOutcome``, ``ReviewDecision``, ``RunnerOptions``, ``RunnerResult`` —
  kernel models (``core.models``).
- ``RunnerBackend``, ``SkillSource`` — kernel ports (``core.ports``).
- ``SkillLoop``, ``SkillLoopConfig`` — the per-mission round loop (``loop``).
- ``Skill``, ``SkillStore`` — the Skill library (``skills.store``).

The kernel names are imported eagerly. ``SkillLoop``, ``SkillLoopConfig``,
``Skill`` and ``SkillStore`` resolve lazily on first attribute access
(PEP 562) because Python imports this package before any submodule, so an
eager ``from .loop import SkillLoop`` here would make ``import
argus_skill.core.paths`` load the Engineer, Reviewer, Skill library, role
prompts and the ``agent_cli`` driver. tests/test_architecture_invariants.py
pins that ``import argus_skill.core.paths`` loads none of them.

Two consequences of deferring: ``__getattr__`` caches the resolved object in
the module namespace, so it runs once per name and later lookups (including
``mock.patch`` / ``monkeypatch``, which set and restore the attribute
directly) bypass it; and an ``ImportError`` raised inside ``argus_skill.loop``
or ``skills.store`` now surfaces at the first attribute access -- including
``hasattr(argus_skill, "SkillLoop")`` -- rather than at ``import argus_skill``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .core.models import (
    LoopOutcome,
    ReviewDecision,
    RunnerOptions,
    RunnerResult,
)
from .core.ports import RunnerBackend, SkillSource

if TYPE_CHECKING:  # static tooling sees the lazy names; runtime resolves them in __getattr__
    from .loop import SkillLoop, SkillLoopConfig
    from .skills.store import Skill, SkillStore

__all__ = [
    "LoopOutcome",
    "ReviewDecision",
    "RunnerBackend",
    "RunnerOptions",
    "RunnerResult",
    "Skill",
    "SkillLoop",
    "SkillLoopConfig",
    "SkillSource",
    "SkillStore",
]

__version__ = "0.1.6"

_LAZY_LOOP_NAMES = frozenset({"SkillLoop", "SkillLoopConfig"})
_LAZY_SKILL_NAMES = frozenset({"Skill", "SkillStore"})


def __getattr__(name: str):  # PEP 562 lazy attrs
    if name in _LAZY_LOOP_NAMES:
        from . import loop  # intentional lazy import (PEP 562)

        value = getattr(loop, name)
    elif name in _LAZY_SKILL_NAMES:
        from .skills import store  # intentional lazy import (PEP 562)

        value = getattr(store, name)
    else:
        raise AttributeError(f"module 'argus_skill' has no attribute {name!r}")
    globals()[name] = value  # cache: later lookups never re-enter __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
