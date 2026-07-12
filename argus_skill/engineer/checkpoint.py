"""Curated working-memory checkpoint for the engineer round loop.

Design principle (operator-stated): *permanent, unfiltered memory is poison*.
Codex's automatic lossy context compaction is that poison metabolising badly:
when one Codex session is resumed across hundreds of rounds it compacts again
and again, each compaction silently dropping working state, until the model
re-orients by re-reading the same docs forever (an amnesia loop).

The antidote is NOT a watchdog. It is to make sessions structurally
short-lived and to carry a *small, curated, value-filtered* handoff across the
session boundary instead of a giant raw history. This module is that handoff:

  * a tiny structured object (goal / done / tried_and_failed / maturing /
    open_blocker / next_step), where ``maturing`` keeps directions that were
    tried but have not YET succeeded and are NOT dead ends — distinct from
    ``tried_and_failed`` so a promising approach is not killed after a single
    losing round,
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
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def _checkpoint_lock(path: Path):
    lock_path = path.with_suffix(".lock")
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

# Hard caps. These are deliberately small: the bound is what compels the
# reviewer to curate (keep only load-bearing items) instead of appending
# forever. Enforced in Python so a verbose/leaky reviewer cannot re-grow the
# poison even if it ignores the prompt.
MAX_DONE_ITEMS = 8
MAX_TRIED_ITEMS = 6
MAX_MATURING_ITEMS = 5
MAX_ITEM_CHARS = 280
MAX_GOAL_CHARS = 400
MAX_BLOCKER_CHARS = 800
MAX_NEXT_CHARS = 600
MAX_ENV_FACTS = 10
# Active-line: a single bold direction the engineer is maturing on a RETAINED
# branch that may sit ABOVE the global-best floor for several rounds. Persisting
# a code POINTER (not just a sentence) is what lets such a line develop
# cumulatively instead of being re-derived from the floor each session.
MAX_ACTIVE_LINE_DESC = 280
MAX_ACTIVE_LINE_PATH = 200
MAX_ACTIVE_LINE_NOTE = 280


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


def _clean_active_line(value: Any) -> dict[str, Any]:
    """Cap/normalize the active-line pointer (fail-soft to ``{}``).

    Keeps a small structured record — ``desc`` (what the line is),
    ``branch_or_path`` (where its code lives so it can be checked out and built
    on), ``rounds_active`` (how long it has been maturing), ``note`` (the next
    refinement). Empty/garbage degrades to ``{}`` so a bad payload never breaks
    the round loop.
    """
    if not isinstance(value, dict):
        return {}
    desc = _clean_str(value.get("desc"), MAX_ACTIVE_LINE_DESC)
    branch = _clean_str(value.get("branch_or_path"), MAX_ACTIVE_LINE_PATH)
    note = _clean_str(value.get("note"), MAX_ACTIVE_LINE_NOTE)
    try:
        rounds_active = int(value.get("rounds_active", 0) or 0)
    except (TypeError, ValueError):
        rounds_active = 0
    rounds_active = max(0, rounds_active)
    if not (desc or branch or note):
        return {}
    return {
        "desc": desc,
        "branch_or_path": branch,
        "rounds_active": rounds_active,
        "note": note,
    }


@dataclass
class CheckpointState:
    """A small curated working-memory handoff between engineer sessions."""

    goal: str = ""
    done: list[str] = field(default_factory=list)
    tried_and_failed: list[str] = field(default_factory=list)
    # Directions/approaches that were TRIED and did not YET succeed, but which
    # the reviewer judges are NOT dead ends — they are worth further refinement
    # before being abandoned (an early attempt at a new approach often
    # underperforms a tuned baseline until it is refined). DISTINCT from
    # ``tried_and_failed`` (genuine dead ends — do NOT repeat). Demoting a
    # direction from ``maturing`` to ``tried_and_failed`` is the reviewer's call
    # once it has had a fair refinement window and still fails.
    maturing: list[str] = field(default_factory=list)
    open_blocker: str = ""
    next_step: str = ""
    # A single bold direction being matured on a RETAINED branch that may sit
    # ABOVE the global-best floor for several rounds (desc / branch_or_path /
    # rounds_active / note). Distinct from the never-lost global-best floor: the
    # floor is the deliverable, the active line is the working bet. Persisting a
    # code POINTER (not just a sentence) is what lets a bold line develop
    # cumulatively across sessions instead of being re-derived from the floor.
    active_line: dict[str, Any] = field(default_factory=dict)
    # Durable environment/infra facts the successor must NOT re-derive (paths,
    # access endpoints, versions, what is ephemeral vs persistent). Same hard-cap
    # curation discipline as the rest: a small value-filtered carry-forward that
    # stops the engineer re-reading/re-discovering the same setup every round.
    env_facts: list[str] = field(default_factory=list)
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
            maturing=_clean_list(raw.get("maturing"), max_items=MAX_MATURING_ITEMS),
            open_blocker=_clean_str(raw.get("open_blocker"), MAX_BLOCKER_CHARS),
            next_step=_clean_str(raw.get("next_step"), MAX_NEXT_CHARS),
            active_line=_clean_active_line(raw.get("active_line")),
            env_facts=_clean_list(raw.get("env_facts"), max_items=MAX_ENV_FACTS),
            round=max(0, round_no),
            updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "done": list(self.done),
            "tried_and_failed": list(self.tried_and_failed),
            "maturing": list(self.maturing),
            "open_blocker": self.open_blocker,
            "next_step": self.next_step,
            "active_line": dict(self.active_line),
            "env_facts": list(self.env_facts),
            "round": self.round,
            "updated_at": self.updated_at,
        }

    def is_empty(self) -> bool:
        return not (
            self.goal
            or self.done
            or self.tried_and_failed
            or self.maturing
            or self.open_blocker
            or self.next_step
            or self.active_line
            or self.env_facts
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
            if self.maturing:
                lines.append(
                    "MATURING DIRECTIONS (tried but NOT yet succeeding — these "
                    "are NOT dead ends: refine / keep developing them before "
                    "abandoning):"
                )
                lines.extend(f"  - {item}" for item in self.maturing)
            if self.active_line:
                al = self.active_line
                bits: list[str] = []
                if al.get("desc"):
                    bits.append(f"direction: {al['desc']}")
                if al.get("branch_or_path"):
                    bits.append(f"code saved at: {al['branch_or_path']}")
                if al.get("rounds_active"):
                    bits.append(f"developed {al['rounds_active']} round(s)")
                if al.get("note"):
                    bits.append(f"next refinement: {al['note']}")
                lines.append(
                    "ACTIVE LINE (a bold direction you are MATURING that may sit "
                    "ABOVE the global-best floor — BUILD ON THIS, do NOT restart "
                    "it from the global-best floor; the floor is the never-lost "
                    "deliverable and stays recoverable separately):"
                )
                lines.append("  - " + " | ".join(bits))
            if self.open_blocker:
                lines.append(f"OPEN BLOCKER: {self.open_blocker}")
            if self.next_step:
                lines.append(f"NEXT STEP: {self.next_step}")
            if self.env_facts:
                lines.append(
                    "ESTABLISHED FACTS (environment/infra — trust these; do NOT "
                    "re-read/re-derive to rediscover them):"
                )
                lines.extend(f"  - {item}" for item in self.env_facts)
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
            "  tried_failed: <approaches you ruled out this turn as genuine "
            "dead ends and why>"
        )
        lines.append(
            "  maturing: <approaches you tried that did NOT yet succeed but are "
            "worth refining further — name the specific next refinement to try; "
            "these are NOT dead ends>"
        )
        lines.append(
            "  active_line: <if you are maturing ONE bold direction that "
            "currently sits ABOVE the global-best floor, record it as desc + "
            "branch_or_path (where its code is saved so a successor can check it "
            "out and build on it) + rounds_active + note (next refinement); '' "
            "if none. This is what lets a bold line develop across rounds "
            "instead of being re-derived from the floor each time.>"
        )
        lines.append(
            "  blocker: <the single most important remaining blocker, or "
            "'none'>"
        )
        lines.append("  next: <the most useful next action>")
        lines.append(
            "  facts: <durable environment/infra facts established this turn "
            "(paths, access endpoints, versions, what's ephemeral vs persistent) "
            "so successors don't re-derive them>"
        )
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
            maturing=list(self.maturing),
            open_blocker=self.open_blocker,
            next_step=self.next_step,
            active_line=dict(self.active_line),
            env_facts=list(self.env_facts),
            round=round_no,
            updated_at=time.time(),
        )

    def cleared_for_jump(self) -> "CheckpointState":
        """Return a copy with the LOCAL trajectory dropped for a regime jump.

        The meta-control layer convenes a regime jump when the promoted floor
        has frozen; continuing the recency-locked local bet (``active_line`` /
        ``maturing`` / ``next_step``) is exactly the trap it is breaking. This
        drops those while KEEPING the durable carry-forward — ``goal``, verified
        ``done``, ``env_facts``, and ``tried_and_failed`` (genuine dead ends stay
        dead) — so the next session opens the new regime fresh instead of
        re-anchoring on the saturated line. The never-lost global-best floor is
        recoverable from ``attempts/`` regardless, so this reset loses nothing
        durable. (Spec §5 context reset.)
        """
        return CheckpointState(
            goal=self.goal,
            done=list(self.done),
            tried_and_failed=list(self.tried_and_failed),
            maturing=[],
            open_blocker=self.open_blocker,
            next_step="",
            active_line={},
            env_facts=list(self.env_facts),
            round=self.round,
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
    tmp: Path | None = None
    try:
        p = Path(path)
        with _checkpoint_lock(p):
            fd, tmp_name = tempfile.mkstemp(
                dir=p.parent,
                prefix=f".{p.name}.",
                suffix=".tmp",
            )
            tmp = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(checkpoint.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, p)
    except OSError:
        return
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
