"""argus.session — Session model: Trace + token-rollover + checkpoint.json continuity.

When a session's token count crosses a threshold, compress the memory, write a fresh
checkpoint, and start a new session. checkpoint.json is the session summary; a new
session reads it first for continuity. (The tree + skills/wiki persist across sessions;
the raw Trace does not.)
"""
from __future__ import annotations
import json, time
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Session:
    root: Path
    token_budget: int = 120_000      # roll over near the model's context window
    tokens: int = 0
    trace: list[str] = field(default_factory=list)   # raw trajectory (not persisted to wiki)

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
        """Compress + save, then start a fresh session that already knows the summary."""
        self.write_checkpoint(summary)
        nxt = Session(root=self.root, token_budget=self.token_budget)
        nxt.trace.append(f"[resumed from checkpoint: {summary.get('headline','')}]")
        return nxt
