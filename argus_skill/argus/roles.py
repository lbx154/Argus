"""argus.roles — Planner / Engineer / Reviewer (+ shared triage helpers).

Each role wraps an ``agent_fn`` (the LLM). The hard rule: **no role judges the win** —
that is the FrozenJudge's job alone. Reviewer gives PROCESS feedback only. The Manager
(user-facing conductor, see manager.py) splits stages + audits skills via the shared
triage helpers below, but never signs "we won".

Every agent owns its OWN Session + checkpoint.json (under ``<root>/agents/<name>/``),
because each LLM context fills independently — the Engineer's fills fastest, the
Reviewer's separately — so each rolls over + resumes on its own.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from .core import Task, Stage, Node, Evidence, Verdict
from .session import Session


# a stub agent for demos/tests; production passes a real LLM callable
def stub_agent(prompt: str) -> str:
    return "ok"


@dataclass
class Agent:
    """Base for every role: its own Session (Trace + token rollover + checkpoint.json)."""
    name: str = "agent"
    agent_fn: Callable[[str], str] = stub_agent
    session: Optional[Session] = None

    def bind_session(self, root: Path) -> "Agent":
        d = Path(root) / "agents" / self.name
        d.mkdir(parents=True, exist_ok=True)
        self.session = Session(root=d)
        return self

    def resume(self) -> dict:
        """Read this agent's own checkpoint on (re)start."""
        return self.session.load_checkpoint() if self.session else {}

    def note(self, event: str, tokens: int = 0, summary: Optional[dict] = None) -> bool:
        """Record to this agent's own Trace; roll over + checkpoint if its context fills."""
        if not self.session:
            return False
        if self.session.add(event, tokens):
            self.session = self.session.rollover(summary or {"headline": f"{self.name} rollover"})
            return True
        return False

    def checkpoint(self, summary: dict) -> None:
        if self.session:
            self.session.write_checkpoint(summary)


# ---- shared triage helpers (one source of truth for Manager + Run) ----
def triage(task: Task) -> str:
    t = task.text.lower()
    if any(k in t for k in ("paper", "benchmark", "论文", "research")):
        return "research"
    if any(k in t for k in ("optimize", "speedrun", "kernel", "bpb", "factor", "sharpe", "优化")):
        return "optimize"
    return "freeform"


def split_stages(task: Task, kind: str) -> list[Stage]:
    if kind == "research":
        order = ["research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission"]
        return [Stage(name=s, template="research") for s in order]
    if kind == "optimize":
        return [Stage(name="optimize", template="optimize")]
    return [Stage(name="do", template="custom")]


def audit_skill(skill) -> bool:
    """Manager approves a Reviewer-curated skill for use."""
    return bool(skill and skill.content)


@dataclass
class Planner(Agent):
    name: str = "planner"

    def plan_loops(self, stage: Stage, max_loops: int = 8) -> list[str]:
        """Return loop tasks for this stage. (Lessons are carried separately and fed
        to the Engineer so it never re-queues a wall an earlier loop already learned.)"""
        return [f"{stage.name}-loop-{i}" for i in range(max_loops)]


@dataclass
class Engineer(Agent):
    name: str = "engineer"

    def propose(self, loop_task: str, idx: int, lessons: list[str]) -> Node:
        """Declare a hypothesis as a flat journal node (informed by prior lessons)."""
        return Node(id=f"n{idx}", hypothesis=loop_task, family="default")

    def candidate(self, node: Node) -> tuple[float, list[str]]:
        """Run the experiment → (metric, artifact refs). Stub returns a placeholder."""
        return (1.0, [node.artifact])

    def lesson(self, ev: Evidence) -> str:
        return "" if ev.passed else (ev.note or "did not clear the floor")


@dataclass
class Reviewer(Agent):
    name: str = "reviewer"

    def feedback(self, node: Node, ev: Evidence) -> Verdict:
        """PROCESS feedback only — never decides the win (that's Evidence)."""
        if ev.passed:
            return Verdict("stage_done", f"{node.hypothesis}: real win, {ev.note}")
        return Verdict("continue", f"{node.hypothesis}: {ev.note} — try a bolder branch")
