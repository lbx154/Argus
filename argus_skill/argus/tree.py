"""argus.tree — the hypothesis tree. Replaces the flat journal, the three
stuck-detectors (basin-hop / islands-reset / meta-saturation), and the brittle
``_journal_has_full_emnlp_gate_success`` done-logic — all collapse into "the frontier".

Failures are lightweight nodes (a one-line lesson), not fat MDs. Lessons propagate
along a branch so the agent never re-hits a wall an earlier sibling already learned.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
from .core import Node, Evidence


class HypothesisTree:
    def __init__(self):
        self.nodes: dict[str, Node] = {}

    # ---- the agent declares a hypothesis; the harness just files it ----
    def add(self, node: Node) -> Node:
        self.nodes[node.id] = node
        return node

    # ---- ONLY the Judge's evidence lands here ----
    def attach_evidence(self, node_id: str, ev: Evidence, lesson: str = "") -> None:
        n = self.nodes[node_id]
        n.evidence = ev
        n.outcome = "PASS-A" if ev.passed else "PASS-B"
        if lesson:
            n.lesson = lesson

    # ---- lesson propagation along a branch (so we don't re-hit a learned wall) ----
    def lessons_for(self, family: str) -> list[str]:
        seen, out = set(), []
        for n in self.nodes.values():
            if n.family == family and n.outcome == "PASS-B" and n.lesson and n.lesson not in seen:
                seen.add(n.lesson)
                out.append(n.lesson)
        return out

    def best(self) -> Optional[Node]:
        winners = [n for n in self.nodes.values() if n.evidence and n.evidence.passed]
        return min(winners, key=lambda n: n.evidence.metric) if winners else None

    # ---- done = frontier exhausted (replaces the brittle journal scan) ----
    def frontier(self) -> list[Node]:
        """Pending nodes worth expanding. Here: anything not yet measured."""
        return [n for n in self.nodes.values() if n.outcome == "pending"]

    def frontier_exhausted(self) -> bool:
        return len(self.frontier()) == 0

    # ---- persistence (sits next to checkpoint.json) ----
    def save(self, path: Path) -> None:
        path.write_text(json.dumps(
            {nid: vars(n) | {"evidence": (vars(n.evidence) if n.evidence else None)}
             for nid, n in self.nodes.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
