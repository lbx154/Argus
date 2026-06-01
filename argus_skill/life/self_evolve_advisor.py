"""Self-evolve advisor (Signal A · trajectory).

Post-mission scanner that surfaces missing-tool patterns from a
finished mission's trajectory as ``self_evolve.missing_tool_advisory``
journal entries. The mint decision ("is this worth minting? was it a
typo? did we work around it?") belongs to the reviewer/planner agent
per skill 04; this module only does the **structural** half.

Extracted from ``argus_skill/life/supervisor.py`` in keeping with
``nssmd/skills/06-keep-files-small.md`` — self-evolve is a separate
responsibility from mission dispatch / lifecycle / budget, so it
gets its own module.

Public surface:

* :class:`SelfEvolveAdvisor` — holds the dedup-by-recent-journal
  state machine; instantiated per-tick from supervisor.

The supervisor uses this via a thin delegate
(``_maybe_journal_self_evolve_advisory`` is just a one-liner that
constructs + calls). Tests can either drive the supervisor delegate
or this class directly.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Iterable

from .memory import BacklogItem, JournalEntry, LifeMemory
from .missing_tool_detector import scan_mission

log = logging.getLogger(__name__)

# Tag the supervisor uses on mint-skill missions; the advisor checks
# this on the SOURCE mission to avoid recursive surfacing (a mint
# mission's own missing-tool noise must not generate fresh advisories).
MINT_SKILL_TAG = "mint-skill"

# Kind written to the journal for each surfaced missing-tool signal.
ADVISORY_KIND = "self_evolve.missing_tool_advisory"

# Recent-journal window size used to dedup re-surfacing of the same
# tool across multiple ticks. Larger = stronger dedup; smaller = the
# agent gets reminded again sooner if it ignored the advisory.
DEFAULT_RECENT_WINDOW = 200


class SelfEvolveAdvisor:
    """Per-tick post-mission scanner. Stateless across constructor
    calls; all state lives in the journal."""

    def __init__(
        self,
        memory: LifeMemory,
        *,
        on_cost: Callable[[JournalEntry], None] | None = None,
        recent_window: int = DEFAULT_RECENT_WINDOW,
    ) -> None:
        self.memory = memory
        self._on_cost = on_cost or (lambda _entry: None)
        self.recent_window = recent_window

    def maybe_journal_advisory(
        self, item: BacklogItem, result: dict[str, Any] | None
    ) -> list[str]:
        """Detect missing-tool signals in the just-finished mission and
        write them to the journal as advisory entries.

        Returns the list of tool_name slugs surfaced this tick (empty
        when no signal, or when every signal was already in the recent
        journal window).

        Does NOT enqueue any BacklogItem — that's the agent's job.
        Reviewer recommends via ``next_action``; planner batch-enqueues
        from accumulated advisories during continuous cycle.

        Fail-soft: the caller (supervisor) wraps this in a try/except
        because a self-evolve issue must never block the main tick.
        """
        # Anti-recursion: a mint-skill mission's own trajectory will
        # include lots of command-not-found / module-not-found noise
        # WHILE the mint mission is itself wrestling with installing
        # the missing tool. Don't surface those as new advisories.
        if MINT_SKILL_TAG in (item.tags or []):
            return []

        agent_messages, check_output_tails, fatal_error = self._unpack_result(result)
        events = self._tail_events_for_item(self.memory, item)

        signals = scan_mission(
            agent_messages=agent_messages,
            check_output_tails=check_output_tails,
            fatal_error=fatal_error,
            events=events,
        )
        if not signals:
            return []

        recent_tools = self._recent_advisory_tools()
        surfaced: list[str] = []
        for sig in signals:
            if sig.tool_name in recent_tools:
                continue
            entry = self._build_advisory_entry(sig, item)
            self.memory.journal.append(entry)
            self._on_cost(entry)
            recent_tools.add(sig.tool_name)
            surfaced.append(sig.tool_name)
        return surfaced

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _unpack_result(
        result: dict[str, Any] | None,
    ) -> tuple[list[str], list[str], str | None]:
        """Pull the safely-available signal sources out of a mission
        result dict. Result shape varies by code path; we read what we
        can find, default the rest."""
        result = result or {}
        agent_messages: list[str] = []
        check_output_tails: list[str] = []
        fatal_error: str | None = None

        if isinstance(result.get("agent_messages"), list):
            agent_messages = [str(m) for m in result["agent_messages"]]
        outcome = result.get("outcome")
        if outcome is not None:
            fatal_error = (
                getattr(outcome, "fatal_error", None)
                or str(getattr(outcome, "stop_reason", "") or "")
                or None
            )
        if isinstance(result.get("checks"), list):
            check_output_tails = [
                str(getattr(c, "output_tail", "") or "")
                for c in result["checks"]
            ]
        return agent_messages, check_output_tails, fatal_error

    @staticmethod
    def _build_advisory_entry(sig: Any, item: BacklogItem) -> JournalEntry:
        evidence_lines = "\n".join(f"- {e}" for e in (sig.evidence or ()))
        return JournalEntry.new(
            kind=ADVISORY_KIND,
            title=f"missing tool: {sig.tool_name}",
            summary=(
                f"{sig.kind}: {sig.context} "
                f"(source mission {item.id})\n{evidence_lines}"
            ),
            tags=[
                "self-evolve",
                "advisory",
                f"kind:{sig.kind}",
                f"tool:{sig.tool_name}",
            ],
        )

    def _recent_advisory_tools(self) -> set[str]:
        """Return tool_name slugs already advised in the recent journal
        window. Used to suppress per-tick re-surfacing of the same
        signal."""
        tools: set[str] = set()
        try:
            entries = list(self.memory.journal.tail(self.recent_window))
        except Exception:  # noqa: BLE001
            return tools
        for entry in entries:
            if getattr(entry, "kind", "") != ADVISORY_KIND:
                continue
            for tag in (getattr(entry, "tags", None) or []):
                if isinstance(tag, str) and tag.startswith("tool:"):
                    tools.add(tag.split(":", 1)[1])
        return tools

    @staticmethod
    def _tail_events_for_item(
        memory: LifeMemory, item: BacklogItem
    ) -> list[dict[str, Any]]:
        """Best-effort: read events.jsonl rows whose ts is at or after
        the mission's ``started_ts``. Returns [] on any I/O / parse
        problem; the detector falls back to the result dict in that case.
        """
        try:
            root = getattr(memory, "root", None)
            if root is None:
                return []
            events_path = Path(root) / "events.jsonl"
            if not events_path.exists():
                return []
            started = float(item.started_ts or item.ts or 0.0)
            out: list[dict[str, Any]] = []
            for line in events_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = ev.get("ts")
                try:
                    ts_f = float(ts) if ts is not None else 0.0
                except (TypeError, ValueError):
                    ts_f = 0.0
                if started == 0.0 or ts_f >= started:
                    out.append(ev)
            return out
        except Exception:  # noqa: BLE001
            return []


__all__ = [
    "MINT_SKILL_TAG",
    "ADVISORY_KIND",
    "DEFAULT_RECENT_WINDOW",
    "SelfEvolveAdvisor",
]
