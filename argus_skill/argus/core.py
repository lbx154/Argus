"""argus.core — data layer for the slimmed Argus.

Plain dataclasses. The roles/orchestrator operate on these; only the Judge
writes ``Evidence`` (the win). Replaces the scattered state in life/ + verticals/.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class Task:
    text: str
    kind: str = "freeform"          # research | optimize | freeform — set by Manager.triage


@dataclass
class Stage:
    name: str
    template: str = "custom"        # research | optimize | custom
    status: str = "pending"         # pending | running | done


@dataclass
class Evidence:
    """The win. ONLY the Judge writes this. The agent can self-report anything;
    only this counts."""
    metric: Optional[float] = None  # frozen-scorer measurement (lower-is-better)
    ci: Optional[float] = None
    n_seed: int = 0
    passed: bool = False
    note: str = ""


@dataclass
class Verdict:
    """The Reviewer's PROCESS feedback — NOT the win."""
    decision: str                   # continue | stage_done | blocked
    feedback: str = ""


@dataclass
class Node:
    """A hypothesis in the tree. Failures are lightweight nodes (a one-line lesson),
    never a fat postmortem MD."""
    id: str
    hypothesis: str
    parent: Optional[str] = None
    family: str = "root"
    artifact: str = ""
    evidence: Optional[Evidence] = None     # written by the Judge
    outcome: str = "pending"        # PASS-A | PASS-B | pending
    lesson: str = ""                # one line
    t: float = field(default_factory=time.time)


@dataclass
class Skill:
    name: str
    content: str
    family: str = ""
    provisional: bool = True        # not proven; confirmed after it helps a later loop
    employed: bool = False          # Manager-approved for use
    version: int = 1
