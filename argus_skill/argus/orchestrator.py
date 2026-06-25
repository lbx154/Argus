"""argus.orchestrator — the slim run loop.

Manager triages + splits stages → Planner plans loops → Engineer ↔ Reviewer per loop,
with the FrozenJudge giving the win and a flat Journal carrying the attempt log.

done = (research) all stages done + gate  OR  (optimize) PASS-A / budget spent.
This replaces life/supervisor/_core.py's tick()+auto-stop+self-evolve wiring.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional
from .core import Task, Node
from .judge import FrozenJudge
from .session import Journal
from .roles import Planner, Engineer, Reviewer, triage, split_stages


@dataclass
class RunConfig:
    budget_loops: int = 30           # hard cap on loops (stand-in for $/GPU budget)


@dataclass
class Run:
    judge: FrozenJudge
    # the Engineer must supply a real candidate(node)->(metric, refs); demo uses a fn
    candidate_fn: Callable[[Node], tuple]
    planner: Planner = field(default_factory=Planner)
    engineer: Engineer = field(default_factory=Engineer)
    reviewer: Reviewer = field(default_factory=Reviewer)
    journal: Journal = field(default_factory=Journal)
    cfg: RunConfig = field(default_factory=RunConfig)
    log: list[str] = field(default_factory=list)

    def _say(self, m: str):
        self.log.append(m)

    def run(self, task: Task) -> dict:
        kind = triage(task)
        task.kind = kind
        stages = split_stages(task, kind)
        self._say(f"[manager] kind={kind} · {len(stages)} stage(s)")

        loops = 0
        for stage in stages:
            stage.status = "running"
            plan = self.planner.plan_loops(stage, max_loops=self.cfg.budget_loops)
            for loop_task in plan:
                if loops >= self.cfg.budget_loops:
                    self._say("[done] budget spent"); break
                node = self.engineer.propose(loop_task, loops, self.journal.lessons())
                loops += 1
                metric, refs = self.candidate_fn(node)          # the real experiment
                ev = self.judge.score(metric, refs)             # THE WIN (frozen, external)
                self.journal.record(node, ev, lesson=self.engineer.lesson(ev))
                verdict = self.reviewer.feedback(node, ev)      # process feedback only
                self._say(f"[loop {loops}] {node.hypothesis} → {ev.metric} {'✅PASS-A' if ev.passed else 'PASS-B'} | {verdict.decision}")
                if kind == "optimize" and ev.passed:
                    stage.status = "done"; break
                if verdict.decision == "stage_done":
                    stage.status = "done"; break
            stage.status = "done"

        best = self.journal.best()
        return {
            "kind": kind, "loops": loops,
            "best": (best.hypothesis if best else None),
            "best_metric": (best.evidence.metric if best else None),
            "floor": self.judge.floor,
            "nodes": len(self.journal.nodes),
        }
