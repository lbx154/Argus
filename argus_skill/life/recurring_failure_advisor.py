"""Recurring-failure advisor (Signal B · trajectory).

Post-mission scanner that surfaces *recurring infrastructure failure*
patterns across missions as ``self_evolve.recurring_failure_advisory``
journal entries. The mint decision ("is this recurrence worth a distilled
debugging skill?") belongs to the reviewer/planner agent per skill 04;
this module only does the **structural** half (detect + count + surface).

Sibling of ``self_evolve_advisor.SelfEvolveAdvisor`` (Signal A). Where
Signal A flags a *missing tool* from a single mission, Signal B flags the
*same failure class recurring across N distinct missions* — the signal
that the agent keeps rediscovering the same fix instead of distilling it.

State lives entirely in the journal:

* Per mission, each detected signature is recorded once as a low-level
  ``self_evolve.failure_observation`` entry (idempotent per mission+sig).
  These are counting rows; they are filtered out of the planner context
  render so they do not crowd it.
* When a signature's distinct-mission count within the lookback window
  reaches ``min_recurrence``, a single visible
  ``self_evolve.recurring_failure_advisory`` entry is surfaced (deduped
  so it is not re-emitted every tick).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .failure_signature_detector import scan_failure_signatures
from .memory import JournalEntry, LifeMemory

# Reuse Signal A's anti-recursion tag + result-unpacking + event-tailing
# so both advisors agree on what a "mint mission" is and how to read a
# mission result.
from .self_evolve_advisor import MINT_SKILL_TAG, SelfEvolveAdvisor

log = logging.getLogger(__name__)

# Journal kinds written by this advisor.
OBSERVATION_KIND = "self_evolve.failure_observation"
ADVISORY_KIND = "self_evolve.recurring_failure_advisory"

# How many DISTINCT missions must hit the same signature (within the
# lookback window) before we surface an advisory.
DEFAULT_MIN_RECURRENCE = 3

# Time-based recurrence window. Entry-count windows undercount on a busy
# 7×24 daemon, so recurrence is measured over wall-clock days.
DEFAULT_LOOKBACK_DAYS = 7.0

# Upper bound on journal rows read per tick (cost guard). Generously
# larger than any realistic number of entries in the lookback window.
_MAX_TAIL = 4000


class RecurringFailureAdvisor:
    """Per-tick post-mission scanner. Stateless across constructor
    calls; all state lives in the journal."""

    def __init__(
        self,
        memory: LifeMemory,
        *,
        on_cost: Callable[[JournalEntry], None] | None = None,
        min_recurrence: int = DEFAULT_MIN_RECURRENCE,
        lookback_days: float = DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        self.memory = memory
        self._on_cost = on_cost or (lambda _entry: None)
        self.min_recurrence = max(2, int(min_recurrence))
        self.lookback_secs = max(0.0, float(lookback_days)) * 86400.0

    def maybe_journal_advisory(
        self, item: Any, result: dict[str, Any] | None
    ) -> list[str]:
        """Record this mission's infra-failure signatures and surface an
        advisory for any signature that has now recurred across enough
        distinct missions.

        Returns the list of signature slugs for which a *new* advisory was
        surfaced this tick (empty when below threshold or already advised).

        Fail-soft: the caller (supervisor) wraps this in try/except.
        """
        # Anti-recursion: a mint-skill mission's own trajectory is full of
        # infra-failure noise WHILE it wrestles with the fix; don't count it.
        if MINT_SKILL_TAG in (getattr(item, "tags", None) or []):
            return []

        agent_messages, check_output_tails, fatal_error = (
            SelfEvolveAdvisor._unpack_result(result)
        )
        events = SelfEvolveAdvisor._tail_events_for_item(self.memory, item)

        sigs = scan_failure_signatures(
            agent_messages=agent_messages,
            check_output_tails=check_output_tails,
            fatal_error=fatal_error,
            events=events,
        )
        if not sigs:
            return []

        mission_id = str(getattr(item, "id", "") or "unknown")
        recent = self._recent_entries()

        surfaced: list[str] = []
        for sig in sigs:
            self._maybe_record_observation(sig, mission_id, recent)
            if self._already_advised(sig.signature, recent):
                continue
            count = self._distinct_mission_count(sig.signature, recent)
            if count >= self.min_recurrence:
                entry = self._build_advisory_entry(sig, count)
                self.memory.journal.append(entry)
                self._on_cost(entry)
                recent.append(entry)
                surfaced.append(sig.signature)
        return surfaced

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _recent_entries(self) -> list[JournalEntry]:
        """Journal rows within the lookback window (bounded read)."""
        try:
            rows = list(self.memory.journal.tail(_MAX_TAIL))
        except Exception:  # noqa: BLE001
            return []
        if self.lookback_secs <= 0:
            return rows
        cutoff = time.time() - self.lookback_secs
        return [e for e in rows if float(getattr(e, "ts", 0.0) or 0.0) >= cutoff]

    def _maybe_record_observation(
        self, sig: Any, mission_id: str, recent: list[JournalEntry]
    ) -> None:
        """Append one observation row per (mission, signature). Idempotent:
        skip if this mission already has an observation for this sig in the
        recent window."""
        sig_tag = f"sig:{sig.signature}"
        mission_tag = f"mission:{mission_id}"
        for e in recent:
            if getattr(e, "kind", "") != OBSERVATION_KIND:
                continue
            tags = getattr(e, "tags", None) or []
            if sig_tag in tags and mission_tag in tags:
                return
        evidence_lines = "\n".join(f"- {x}" for x in (sig.evidence or ()))
        entry = JournalEntry.new(
            kind=OBSERVATION_KIND,
            title=f"infra failure: {sig.signature}",
            summary=(
                f"{sig.category}: {sig.context} (mission {mission_id})\n"
                f"{evidence_lines}"
            ),
            tags=[
                "self-evolve",
                "failure-observation",
                sig_tag,
                mission_tag,
                f"category:{sig.category}",
            ],
        )
        self.memory.journal.append(entry)
        self._on_cost(entry)
        recent.append(entry)

    def _distinct_mission_count(
        self, signature: str, recent: list[JournalEntry]
    ) -> int:
        """Count DISTINCT missions that hit ``signature`` in the window.

        Counts the set of ``mission:<id>`` tags rather than raw rows so a
        duplicate observation (idempotency miss, race, replay) cannot
        inflate the count.
        """
        sig_tag = f"sig:{signature}"
        missions: set[str] = set()
        for e in recent:
            if getattr(e, "kind", "") != OBSERVATION_KIND:
                continue
            tags = getattr(e, "tags", None) or []
            if sig_tag not in tags:
                continue
            for t in tags:
                if isinstance(t, str) and t.startswith("mission:"):
                    missions.add(t.split(":", 1)[1])
        return len(missions)

    def _already_advised(
        self, signature: str, recent: list[JournalEntry]
    ) -> bool:
        """True if an advisory for this signature is already in the recent
        window (dedup — don't re-surface every tick)."""
        sig_tag = f"sig:{signature}"
        for e in recent:
            if getattr(e, "kind", "") != ADVISORY_KIND:
                continue
            if sig_tag in (getattr(e, "tags", None) or []):
                return True
        return False

    @staticmethod
    def _build_advisory_entry(sig: Any, count: int) -> JournalEntry:
        evidence_lines = "\n".join(f"- {x}" for x in (sig.evidence or ()))
        return JournalEntry.new(
            kind=ADVISORY_KIND,
            title=f"recurring infra failure: {sig.signature} (×{count})",
            summary=(
                f"The infrastructure failure class '{sig.signature}' "
                f"({sig.category}: {sig.context}) has now recurred across "
                f"{count} distinct missions. The agent keeps rediscovering "
                f"the fix instead of distilling it. Consider whether an "
                f"execution-measurable debugging/playbook skill is warranted "
                f"(e.g. a script that detects the failure and applies + "
                f"verifies the fix). Mint only if the fix is verifiable by "
                f"re-running and asserting the error is gone.\n{evidence_lines}"
            ),
            tags=[
                "self-evolve",
                "advisory",
                "recurring-failure",
                f"sig:{sig.signature}",
                f"category:{sig.category}",
            ],
        )


__all__ = [
    "OBSERVATION_KIND",
    "ADVISORY_KIND",
    "DEFAULT_MIN_RECURRENCE",
    "DEFAULT_LOOKBACK_DAYS",
    "RecurringFailureAdvisor",
]
