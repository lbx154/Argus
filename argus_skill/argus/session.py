"""argus.session — Session model: Trace + a flat Journal + token-rollover + checkpoint.

When a session's token count crosses a threshold, compress the memory, write a fresh
checkpoint, and start a new session. checkpoint.json is the session summary; a new
session reads it first for continuity.

The Journal is the structured Trace: a FLAT log of attempts (no tree, no branches, no
frontier). It only tracks the best PASS-A so far and a deduped list of one-line lessons
from PASS-B attempts, so the Engineer doesn't re-hit a wall it already learned.
(Raw Trace is not persisted across sessions; best+lessons carry via the checkpoint.)
"""
from __future__ import annotations
import json, time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from .core import Node, Evidence


class Journal:
    """Flat attempt-log. Replaces the old HypothesisTree — same bookkeeping, no tree."""

    def __init__(self):
        self.nodes: list[Node] = []

    def record(self, node: Node, ev: Evidence, lesson: str = "") -> Node:
        node.evidence = ev
        node.outcome = "PASS-A" if ev.passed else "PASS-B"
        if lesson:
            node.lesson = lesson
        self.nodes.append(node)
        return node

    def best(self) -> Optional[Node]:
        winners = [n for n in self.nodes if n.evidence and n.evidence.passed]
        return min(winners, key=lambda n: n.evidence.metric) if winners else None

    def lessons(self) -> list[str]:
        seen, out = set(), []
        for n in self.nodes:
            if n.outcome == "PASS-B" and n.lesson and n.lesson not in seen:
                seen.add(n.lesson)
                out.append(n.lesson)
        return out


@dataclass
class Session:
    root: Path
    token_budget: int = 120_000      # roll over near the model's context window
    tokens: int = 0
    trace: list[str] = field(default_factory=list)   # raw trajectory (not persisted to wiki)
    journal: Journal = field(default_factory=Journal)  # structured Trace (best + lessons)

    def add(self, event: str, tokens: int = 0) -> bool:
        """Append to the trace; return True if a rollover is due."""
        self.trace.append(event)
        self.tokens += tokens
        return self.tokens >= self.token_budget

    def checkpoint_path(self) -> Path:
        return self.root / "checkpoint.json"

    def write_checkpoint(self, summary: dict) -> None:
        summary = dict(summary)
        summary["_ts"] = time.time()
        self.checkpoint_path().write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    def load_checkpoint(self) -> dict:
        p = self.checkpoint_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def rollover(self, summary: dict) -> "Session":
        """Compress + save, then start a fresh session that already knows the summary.
        best+lessons ride along in the checkpoint; the raw Trace is dropped."""
        summary = dict(summary)
        summary.setdefault("lessons", self.journal.lessons())
        self.write_checkpoint(summary)
        nxt = Session(root=self.root, token_budget=self.token_budget)
        nxt.trace.append(f"[resumed from checkpoint: {summary.get('headline','')}]")
        return nxt
