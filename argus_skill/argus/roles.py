"""argus.roles — Planner / Engineer / Reviewer (+ shared triage helpers).

Each role wraps an ``agent_fn`` (the LLM). The hard rule: **no role judges the win** —
that is the FrozenJudge's job alone. Reviewer gives PROCESS feedback only. The Manager
(user-facing conductor, see manager.py) splits stages + audits skills via the shared
triage helpers below, but never signs "we won".
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional
from .core import Task, Stage, Node, Evidence, Verdict


# a stub agent for demos/tests; production passes a real LLM callable
def stub_agent(prompt: str) -> str:
    return "ok"


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
class Planner:
    agent_fn: Callable[[str], str] = stub_agent

    def plan_loops(self, stage: Stage, tree, max_loops: int = 8) -> list[str]:
        """Return loop tasks for this stage. Reads the tree frontier + propagated
        lessons so it never re-queues a wall an earlier branch already learned."""
        return [f"{stage.name}-loop-{i}" for i in range(max_loops)]


@dataclass
class Engineer:
    agent_fn: Callable[[str], str] = stub_agent

    def propose(self, loop_task: str, tree, lessons: list[str]) -> Node:
        """Declare a hypothesis as a tree node (parent declared by the agent)."""
        nid = f"n{len(tree.nodes)}"
        return Node(id=nid, hypothesis=loop_task, family="default")

    def candidate(self, node: Node) -> tuple[float, list[str]]:
        """Run the experiment → (metric, artifact refs). Stub returns a placeholder."""
        return (1.0, [node.artifact])

    def lesson(self, ev: Evidence) -> str:
        return "" if ev.passed else (ev.note or "did not clear the floor")


@dataclass
class Reviewer:
    agent_fn: Callable[[str], str] = stub_agent

    def feedback(self, node: Node, ev: Evidence) -> Verdict:
        """PROCESS feedback only — never decides the win (that's Evidence)."""
        if ev.passed:
            return Verdict("stage_done", f"{node.hypothesis}: real win, {ev.note}")
        return Verdict("continue", f"{node.hypothesis}: {ev.note} — try a bolder branch")
