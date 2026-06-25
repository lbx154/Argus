"""argus.manager — the user-facing conductor.

Manager talks to the user, decides if a Task is "regular", splits it into Stages
(or uses a preset template like the research stages), then drives the loop:
  Manager → (Research-first) → Planner → Engineer ↔ Reviewer ↔ FrozenJudge ↔ Journal
It also AUDITS Reviewer-curated skills before they may be used. Every agent owns its
OWN Session + checkpoint.json and rolls over independently when its context fills.
The Manager NEVER judges the win — that is the FrozenJudge's alone.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from .core import Task, Stage, Node, Skill
from .judge import FrozenJudge
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
    log: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.project_root = Path(self.project_root)
        self.wiki = Wiki(self.project_root)
        self.skills = SkillStore(self.project_root / "skills")
        self.selector = SkillSelector(self.skills)
        # every agent has its OWN Session + checkpoint.json. The Manager (conductor) owns
        # the top-level checkpoint; each worker gets <root>/agents/<name>/checkpoint.json.
        self.session = Session(root=self.project_root)
        for a in (self.planner, self.engineer, self.reviewer):
            a.bind_session(self.project_root)

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
        carry_lessons = list(prior.get("lessons", []))     # cross-session lesson carry
        if prior:
            self.log.append(f"[session] resumed: {prior.get('headline','')}")
        for a in (self.planner, self.engineer, self.reviewer):  # each agent resumes its own
            cp = a.resume()
            if cp:
                self.log.append(f"[{a.name}] resumed: {cp.get('headline','')}")
        task = Task(user_task)
        kind = self.triage(task); task.kind = kind
        self.log.append(f"[manager] kind={kind}")

        # Research-first: every task's first stage is Research → feed the wiki
        finding = research(self.wiki, user_task, self.search_fn)
        self.log.append(f"[research] filed wiki note ({len(finding)} chars)")

        stages = self.split_stages(task, kind)
        jrnl = self.session.journal
        loops = 0
        for stage in stages:
            stage.status = "running"
            plan = self.planner.plan_loops(stage, max_loops=self.budget_loops)
            self.planner.note(f"planned {stage.name}: {len(plan)} loops", tokens=200)
            for loop_task in plan:
                if loops >= self.budget_loops:
                    self.log.append("[done] budget spent"); break
                picked = self.selector.select(loop_task)           # Agent self-selects a skill
                node = self.engineer.propose(loop_task, loops, carry_lessons + jrnl.lessons())
                loops += 1
                metric, refs = self.candidate_fn(node)             # real experiment
                ev = self.judge.score(metric, refs)                # THE WIN (frozen, external)
                jrnl.record(node, ev, lesson=self.engineer.lesson(ev))
                self.engineer.note(f"{loop_task} → {ev.metric} {'PASS-A' if ev.passed else 'PASS-B'}",
                                   tokens=600)                      # engineer's own context fills fastest
                v = self.reviewer.feedback(node, ev)               # process feedback only
                self.reviewer.note(f"{loop_task}: {v.decision}", tokens=300)
                self.log.append(f"[loop {loops}] {loop_task}{' +'+picked.name if picked else ''} "
                                f"→ {ev.metric} {'✅PASS-A' if ev.passed else 'PASS-B'} | {v.decision}")
                rolled = self.session.add(self.log[-1], tokens=400)
                if rolled:
                    carry_lessons = carry_lessons + jrnl.lessons()
                    self.session = self.session.rollover(self._summary(loops))
                    jrnl = self.session.journal
                    self.log.append("[session] token budget hit → compressed + new session")
                if (kind == "optimize" and ev.passed) or v.decision == "stage_done":
                    stage.status = "done"; break
            stage.status = "done"

        # Reviewer curates a skill from the trace; Manager audits → employ
        best = jrnl.best()
        if best:
            sk = self.skills.distill_from_trace(f"win-{best.id}", self.session.trace, family="default")
            if self.audit_skill(sk):
                self.skills.confirm(sk.name); self.skills.employ(sk.name)
                self.log.append(f"[skill] curated + audited + employed: {sk.name}")

        # every agent persists its own checkpoint.json
        self.session.write_checkpoint(self._summary(loops))       # manager / top-level
        self.planner.checkpoint({"headline": f"planned {len(stages)} stage(s)", "loops": loops})
        self.engineer.checkpoint(self._summary(loops))
        self.reviewer.checkpoint({"headline": f"reviewed {loops} loop(s)", "loops": loops})
        return {"kind": kind, "loops": loops,
                "best": (best.hypothesis if best else None),
                "best_metric": (best.evidence.metric if best else None),
                "floor": self.judge.floor, "nodes": len(jrnl.nodes),
                "skills": len(self.skills.list())}

    def _summary(self, loops: int) -> dict:
        b = self.session.journal.best()
        return {"headline": (f"PASS-A {b.evidence.metric}" if b else "no PASS-A yet"),
                "loops": loops, "lessons": self.session.journal.lessons(),
                "floor": self.judge.floor}
