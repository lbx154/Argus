"""argus.manager — the user-facing conductor.

Manager talks to the user, decides if a Task is "regular", splits it into Stages
(or uses a preset template like the research stages), then drives the loop:
  Manager → (Research-first) → Planner → Engineer ↔ Reviewer ↔ FrozenJudge ↔ Tree
It also AUDITS Reviewer-curated skills before they may be used, and rolls the Session
over (compress + checkpoint.json) when tokens run out. It NEVER judges the win.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from .core import Task, Stage, Node, Skill
from .judge import FrozenJudge
from .tree import HypothesisTree
from .session import Session
from .skills import SkillStore, SkillSelector
from .wiki import Wiki, research
from .roles import Planner, Engineer, Reviewer, stub_agent, triage, split_stages, audit_skill


@dataclass
class Manager:
    project_root: Path
    judge: FrozenJudge
    candidate_fn: Callable[[Node], tuple]                 # Engineer's real experiment → (metric, refs)
    agent_fn: Callable[[str], str] = stub_agent
    search_fn: Optional[Callable[[str], str]] = None
    budget_loops: int = 30
    # sub-systems (auto-wired)
    planner: Planner = field(default_factory=Planner)
    engineer: Engineer = field(default_factory=Engineer)
    reviewer: Reviewer = field(default_factory=Reviewer)
    tree: HypothesisTree = field(default_factory=HypothesisTree)
    log: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.project_root = Path(self.project_root)
        self.wiki = Wiki(self.project_root)
        self.skills = SkillStore(self.project_root / "skills")
        self.selector = SkillSelector(self.skills)
        self.session = Session(root=self.project_root)

    # ---- task routing (shared helpers; Manager owns the policy) ----
    def triage(self, task: Task) -> str:
        return triage(task)

    def split_stages(self, task: Task, kind: str) -> list[Stage]:
        return split_stages(task, kind)

    def audit_skill(self, skill: Skill) -> bool:
        """Approve a Reviewer-curated skill for use (employed)."""
        return audit_skill(skill)

    # ---- the user-facing entry ----
    def handle(self, user_task: str) -> dict:
        prior = self.session.load_checkpoint()
        if prior:
            self.log.append(f"[session] resumed: {prior.get('headline','')}")
        task = Task(user_task)
        kind = self.triage(task); task.kind = kind
        self.log.append(f"[manager] kind={kind}")

        # Research-first: every task's first stage is Research → feed the wiki
        finding = research(self.wiki, user_task, self.search_fn)
        self.log.append(f"[research] filed wiki note ({len(finding)} chars)")

        stages = self.split_stages(task, kind)
        loops = 0
        for stage in stages:
            stage.status = "running"
            for loop_task in self.planner.plan_loops(stage, self.tree, max_loops=self.budget_loops):
                if loops >= self.budget_loops:
                    self.log.append("[done] budget spent"); break
                loops += 1
                picked = self.selector.select(loop_task)           # Agent self-selects a skill
                node = self.engineer.propose(loop_task, self.tree, self.tree.lessons_for("default"))
                self.tree.add(node)
                metric, refs = self.candidate_fn(node)             # real experiment
                ev = self.judge.score(metric, refs)                # THE WIN (frozen, external)
                self.tree.attach_evidence(node.id, ev, lesson=self.engineer.lesson(ev))
                v = self.reviewer.feedback(node, ev)               # process feedback only
                self.log.append(f"[loop {loops}] {loop_task}{' +'+picked.name if picked else ''} "
                                f"→ {ev.metric} {'✅PASS-A' if ev.passed else 'PASS-B'} | {v.decision}")
                rolled = self.session.add(self.log[-1], tokens=400)
                if rolled:
                    self.session = self.session.rollover(self._summary(loops))
                    self.log.append("[session] token budget hit → compressed + new session")
                if (kind == "optimize" and ev.passed) or v.decision == "stage_done":
                    stage.status = "done"; break
            stage.status = "done"

        # Reviewer curates a skill from the trace; Manager audits → employ
        best = self.tree.best()
        if best:
            sk = self.skills.distill_from_trace(f"win-{best.id}", self.session.trace, family="default")
            if self.audit_skill(sk):
                self.skills.confirm(sk.name); self.skills.employ(sk.name)
                self.log.append(f"[skill] curated + audited + employed: {sk.name}")

        self.session.write_checkpoint(self._summary(loops))
        return {"kind": kind, "loops": loops,
                "best": (best.hypothesis if best else None),
                "best_metric": (best.evidence.metric if best else None),
                "floor": self.judge.floor, "nodes": len(self.tree.nodes),
                "skills": len(self.skills.list())}

    def _summary(self, loops: int) -> dict:
        b = self.tree.best()
        return {"headline": (f"PASS-A {b.evidence.metric}" if b else "no PASS-A yet"),
                "loops": loops, "frontier": len(self.tree.frontier()), "floor": self.judge.floor}
