"""Curated working-memory checkpoint for the engineer round loop.

Design principle (operator-stated): *permanent, unfiltered memory is poison*.
Codex's automatic lossy context compaction is that poison metabolising badly:
when one Codex session is resumed across hundreds of rounds it compacts again
and again, each compaction silently dropping working state, until the model
re-orients by re-reading the same docs forever (an amnesia loop).

The antidote is NOT a watchdog. It is to make sessions structurally
short-lived and to carry a *small, curated, value-filtered* handoff across the
session boundary instead of a giant raw history. This module is that handoff:

  * a tiny structured object (goal / done / tried_and_failed / open_blocker /
    next_step),
  * with HARD CAPS enforced in Python (not just in the prompt) — the cap is the
    forcing function for curation: you cannot keep everything, so the author
    MUST forget the least valuable items (deletion = the active antidote),
  * authored each round by the reviewer (the memory auditor) from the
    engineer's own end-of-turn handoff proposal,
  * persisted to disk so it survives a session roll / crash, while the
    ground-truth artifacts (files, results, logs) stay inert on disk and remain
    re-summonable — which is what makes bold deletion safe.

The checkpoint is the agent's *working* memory, not its archive.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Hard caps. These are deliberately small: the bound is what compels the
# reviewer to curate (keep only load-bearing items) instead of appending
# forever. Enforced in Python so a verbose/leaky reviewer cannot re-grow the
# poison even if it ignores the prompt.
MAX_DONE_ITEMS = 8
MAX_TRIED_ITEMS = 6
MAX_ITEM_CHARS = 280
MAX_GOAL_CHARS = 400
MAX_BLOCKER_CHARS = 800
MAX_NEXT_CHARS = 600


def _clean_str(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _clean_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for entry in value:
        text = _clean_str(entry, MAX_ITEM_CHARS)
        if not text:
            continue
        key = text.casefold()
        if key in seen:  # de-dupe: repeated memory is low value
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


@dataclass
class CheckpointState:
    """A small curated working-memory handoff between engineer sessions."""

    goal: str = ""
    done: list[str] = field(default_factory=list)
    tried_and_failed: list[str] = field(default_factory=list)
    open_blocker: str = ""
    next_step: str = ""
    round: int = 0
    updated_at: float = 0.0

    @classmethod
    def from_dict(cls, raw: Any) -> "CheckpointState":
        """Build a capped checkpoint from arbitrary (LLM/JSON) input.

        Fail-soft: anything malformed degrades to empty fields rather than
        raising, so a bad reviewer payload can never break the round loop.
        """
        if not isinstance(raw, dict):
            return cls()
        try:
            round_no = int(raw.get("round", 0) or 0)
        except (TypeError, ValueError):
            round_no = 0
        try:
            updated_at = float(raw.get("updated_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        return cls(
            goal=_clean_str(raw.get("goal"), MAX_GOAL_CHARS),
            done=_clean_list(raw.get("done"), max_items=MAX_DONE_ITEMS),
            tried_and_failed=_clean_list(
                raw.get("tried_and_failed"), max_items=MAX_TRIED_ITEMS
            ),
            open_blocker=_clean_str(raw.get("open_blocker"), MAX_BLOCKER_CHARS),
            next_step=_clean_str(raw.get("next_step"), MAX_NEXT_CHARS),
            round=max(0, round_no),
            updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "done": list(self.done),
            "tried_and_failed": list(self.tried_and_failed),
            "open_blocker": self.open_blocker,
            "next_step": self.next_step,
            "round": self.round,
            "updated_at": self.updated_at,
        }

    def is_empty(self) -> bool:
        return not (
            self.goal
            or self.done
            or self.tried_and_failed
            or self.open_blocker
            or self.next_step
        )

    def render_for_engineer(self) -> str:
        """Render the curated memory block injected into the engineer prompt.

        Always includes the handoff instruction (so every session, even the
        first, ends by proposing its successor's memory). The memory section
        itself is shown only when there is something curated to show.
        """
        lines: list[str] = []
        lines.append(
            "== CURATED WORKING MEMORY (handoff from your predecessor) =="
        )
        if self.is_empty():
            lines.append(
                "(No prior memory yet — this is the first session on this "
                "mission.)"
            )
        else:
            lines.append(
                "This is your ONLY memory of earlier rounds. The raw session "
                "history was intentionally dropped to avoid context rot — "
                "trust this block, and re-read ground-truth files on disk "
                "when you need detail."
            )
            if self.goal:
                lines.append(f"GOAL: {self.goal}")
            if self.done:
                lines.append("DONE (verified — do not redo):")
                lines.extend(f"  - {item}" for item in self.done)
            if self.tried_and_failed:
                lines.append("TRIED & FAILED (dead ends — do NOT repeat):")
                lines.extend(f"  - {item}" for item in self.tried_and_failed)
            if self.open_blocker:
                lines.append(f"OPEN BLOCKER: {self.open_blocker}")
            if self.next_step:
                lines.append(f"NEXT STEP: {self.next_step}")
        lines.append("")
        lines.append(
            "At the END of your turn, output a concise HANDOFF for your "
            "successor (it starts fresh with ONLY this):"
        )
        lines.append("HANDOFF:")
        lines.append(
            "  done: <what you VERIFIABLY completed this turn, with the "
            "command/file that proves it>"
        )
        lines.append(
            "  tried_failed: <approaches you ruled out this turn and why>"
        )
        lines.append(
            "  blocker: <the single most important remaining blocker, or "
            "'none'>"
        )
        lines.append("  next: <the most useful next action>")
        lines.append(
            "Keep it short and high-value. Omit anything that would not change "
            "what your successor does next."
        )
        return "\n".join(lines)

    def stamped(self, *, round_no: int) -> "CheckpointState":
        """Return a copy with round/updated_at stamped."""
        return CheckpointState(
            goal=self.goal,
            done=list(self.done),
            tried_and_failed=list(self.tried_and_failed),
            open_blocker=self.open_blocker,
            next_step=self.next_step,
            round=round_no,
            updated_at=time.time(),
        )


def load_checkpoint(path: Path | None) -> CheckpointState:
    """Load a checkpoint from disk, fail-soft to empty."""
    if path is None:
        return CheckpointState()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckpointState()
    return CheckpointState.from_dict(raw)


def save_checkpoint(path: Path | None, checkpoint: CheckpointState) -> None:
    """Persist a checkpoint to disk, fail-soft (never break the loop)."""
    if path is None:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return
