"""``LifeSupervisor`` — owns the outer process, runs missions back-to-back.

Per the rubber-duck critique:

- Supervisor (not observer): we OWN the outer loop and call
  ``MissionExecutor.execute(...)`` once per backlog item. We never try
  to push ``/run`` into a finished single-mission daemon.
- Single inbox owner: we don't tail any JsonlCommandBus. The optional
  ``user_inbox`` callable lets a host process feed user-provided
  high-priority objectives into the supervisor's own queue without two
  consumers racing on the same offset file.
- Bounded autonomy: ``LifeBudget`` enforces a per-mission preflight cap
  AND a daily cap. Defaults are conservative (max 3 autonomous missions
  in one supervisor run, $1/mission, $5/day).
- Memory injection is a separate channel (``prelude_context``) — the
  objective string passed to the executor is unmodified, so skill
  matching, mission-id hashing, and reviewer prompts are unaffected.
- Idle = sleep, not spin. We poll every 5 seconds when there's nothing
  to do.

The supervisor is intentionally **synchronous**: one mission at a
time, no thread pool. That matches "an agent with continuity" — the
agent is doing one thing, then the next, like a person.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.ports import EventSink
from .memory import (
    Backlog,
    BacklogItem,
    Journal,
    JournalEntry,
    LifeMemory,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@dataclass
class LifeBudget:
    """Layered cost / iteration limits.

    Enforcement points:

    1. **Preflight per-mission cap**: before starting a backlog item we
       refuse if ``item.max_cost_usd > per_mission_cap_usd`` OR if
       ``daily_remaining < per_mission_cap_usd``. Either condition
       pauses the supervisor with a journal entry — we do not silently
       trim caps.
    2. **Daily cap**: cumulative ``cost_usd`` from journal entries
       whose timestamp is ≥ start-of-current-day-local. The supervisor
       refreshes this number on each loop tick so a long-running
       supervisor honours UTC day rollover.
    3. **Iteration cap**: hard count of autonomous missions completed
       in this supervisor run (NOT cumulative across restarts). Once
       reached, supervisor exits cleanly even if backlog is non-empty.

    Field semantics:

    - ``per_mission_cap_usd``: the highest a single mission is allowed
      to cost (sum of engineer + reviewer + scientist tokens × prices).
    - ``daily_cap_usd``: ceiling on summed cost of mission entries in
      ``journal.jsonl`` whose timestamp falls in the current local day.
    - ``max_missions``: hard cap on missions run by THIS supervisor
      process (resets per ``LifeSupervisor`` instance).
    """

    per_mission_cap_usd: float = 1.0
    daily_cap_usd: float = 5.0
    max_missions: int = 3

    def remaining_today(self, journal: Journal, *, now: float | None = None) -> float:
        """USD remaining in today's budget."""
        now = now if now is not None else time.time()
        # Local day start.
        local = time.localtime(now)
        day_start = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        spent = journal.total_cost_since(day_start)
        return max(0.0, float(self.daily_cap_usd) - float(spent))

    def can_start(
        self,
        *,
        item: BacklogItem,
        journal: Journal,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Return ``(allowed, reason)``. ``reason`` is empty when allowed."""
        if item.max_cost_usd > self.per_mission_cap_usd:
            return False, (
                f"item max_cost_usd=${item.max_cost_usd:.2f} exceeds "
                f"per-mission cap ${self.per_mission_cap_usd:.2f}"
            )
        remain = self.remaining_today(journal, now=now)
        if remain < item.max_cost_usd:
            return False, (
                f"daily budget remaining ${remain:.2f} < "
                f"item cap ${item.max_cost_usd:.2f}"
            )
        return True, ""


# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------

# Default Azure-style USD prices per million tokens. The supervisor only
# uses these to gate against the budget *before* the next mission; it
# does not bill the user. Same defaults as benchmarks/swebench_pro/runner.py.
_DEFAULT_PRICES = {
    "gpt-5.4": (1.25, 10.0),
    "gpt-5.4-mini": (0.25, 2.0),
    "gpt-5.2": (1.25, 10.0),
    "gpt-5.2-codex": (1.25, 10.0),
}


def _price_for(model: str) -> tuple[float, float]:
    """USD per million ``(input, output)`` tokens for ``model``."""
    if not model:
        return _DEFAULT_PRICES["gpt-5.4-mini"]
    if model in _DEFAULT_PRICES:
        return _DEFAULT_PRICES[model]
    # Fallback by family heuristic.
    if "mini" in model:
        return _DEFAULT_PRICES["gpt-5.4-mini"]
    return _DEFAULT_PRICES["gpt-5.4"]


class _CostTrackingSink:
    """Wraps an ``EventSink`` to accumulate token counts.

    The mission engine emits ``round.main.completed`` and
    ``round.review.completed`` events that already carry per-call
    ``input_tokens`` / ``output_tokens`` (Phase-2 instrumentation). We
    fold them into running totals and forward every event downstream
    unchanged.
    """

    def __init__(
        self,
        downstream: EventSink,
        *,
        engineer_model: str,
        reviewer_model: str,
    ) -> None:
        self.downstream = downstream
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self.engineer_input_tokens = 0
        self.engineer_output_tokens = 0
        self.reviewer_input_tokens = 0
        self.reviewer_output_tokens = 0

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "round.main.completed":
                self.engineer_input_tokens += int(event.get("input_tokens", 0) or 0)
                self.engineer_output_tokens += int(event.get("output_tokens", 0) or 0)
            elif kind == "round.review.completed":
                self.reviewer_input_tokens += int(event.get("input_tokens", 0) or 0)
                self.reviewer_output_tokens += int(event.get("output_tokens", 0) or 0)
        except Exception:  # noqa: BLE001
            log.debug("cost-tracking sink ignored malformed event", exc_info=True)
        # Always forward.
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream event sink raised; continuing")

    def total_usd(self) -> float:
        in_eng, out_eng = _price_for(self.engineer_model)
        in_rev, out_rev = _price_for(self.reviewer_model)
        return (
            self.engineer_input_tokens * in_eng / 1_000_000
            + self.engineer_output_tokens * out_eng / 1_000_000
            + self.reviewer_input_tokens * in_rev / 1_000_000
            + self.reviewer_output_tokens * out_rev / 1_000_000
        )

    def total_input_tokens(self) -> int:
        return self.engineer_input_tokens + self.reviewer_input_tokens

    def total_output_tokens(self) -> int:
        return self.engineer_output_tokens + self.reviewer_output_tokens


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

@dataclass
class LifeSupervisorConfig:
    """Knobs for one ``LifeSupervisor`` run."""

    budget: LifeBudget = field(default_factory=LifeBudget)
    poll_interval_seconds: float = 5.0
    # Highest-level kill switch — the supervisor checks this between
    # missions. The CLI sets it on SIGTERM/SIGINT.
    stop_event: threading.Event | None = None


# ----- thin protocol describing what we need from a MissionExecutor --------

class _MissionRunner:
    """Structural type for the MissionExecutor we drive.

    We keep this loose so tests can substitute a fake without dragging
    ArgusBot in. Real callers pass an ``argus_skill.daemon.mission_executor.MissionExecutor``.
    """

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> Any:  # MissionOutcome
        raise NotImplementedError


class LifeSupervisor:
    """Cross-mission scheduler.

    Public API:

    - :meth:`run` — drive missions until backlog is exhausted, the
      iteration cap is hit, the budget is tripped, or ``stop_event``
      is set. Returns a summary dict (mission count, costs, statuses).

    - :meth:`tick` — process a single backlog item if available; useful
      for tests and CLI ``life next``.

    Memory wiring:

    - Before each mission, we render ``LifeMemory.render_prelude(...)``
      using the live objective and forward it as ``prelude_context``.
    - After each mission, we append a ``mission_complete`` /
      ``mission_failed`` journal entry so the next mission can recall it.
    """

    def __init__(
        self,
        *,
        memory: LifeMemory,
        runner: _MissionRunner,
        sink: EventSink,
        config: LifeSupervisorConfig | None = None,
        engineer_model: str = "gpt-5.4-mini",
        reviewer_model: str = "gpt-5.4",
    ) -> None:
        self.memory = memory
        self.runner = runner
        self.sink = sink
        self.config = config or LifeSupervisorConfig()
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self._missions_started = 0

    # ------------------------------------------------------------------
    # Public driving methods
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Drive missions until a stop condition. Returns a summary."""
        results: list[dict[str, Any]] = []
        while True:
            stop_reason = self._maybe_stop()
            if stop_reason:
                self._emit_status(stop_reason)
                break
            outcome = self.tick()
            if outcome is None:
                # Backlog empty — sleep then re-check (so user-added
                # items via the file get picked up). Sleep is bounded
                # by the stop_event so a Ctrl-C shuts us down quickly.
                if self._wait_idle():
                    self._emit_status("stop requested while idle")
                    break
                # Re-check: if backlog still empty, exit cleanly so
                # `life run --once` semantics work in tests.
                if self.memory.backlog.next_pending() is None:
                    self._emit_status("backlog empty; exiting")
                    break
                continue
            results.append(outcome)
            # Stop conditions that ``tick`` signals via the result dict
            # (budget pause leaves the item PENDING on purpose so a
            # later supervisor run can retry — but for THIS run we must
            # not spin on the same blocked item).
            if outcome.get("status") in {"budget_pause", "iteration_cap"}:
                break
        return {
            "missions_started": self._missions_started,
            "results": results,
        }

    def tick(self) -> dict[str, Any] | None:
        """Process at most one backlog item. Returns its result dict or
        ``None`` if nothing was eligible to run."""
        item = self.memory.backlog.next_pending()
        if item is None:
            return None

        ok, reason = self.config.budget.can_start(
            item=item, journal=self.memory.journal
        )
        if not ok:
            # Don't fail the item — it'll be retried next supervisor
            # run when the daily cap rolls over. Just journal it and
            # signal the caller to exit cleanly.
            self._emit_status(f"budget block: {reason}")
            self.memory.journal.append(
                JournalEntry.new(
                    kind="budget_pause",
                    title=f"paused before '{item.title}'",
                    summary=reason,
                    tags=["budget"],
                )
            )
            return {"status": "budget_pause", "item_id": item.id, "reason": reason}

        if self._missions_started >= self.config.budget.max_missions:
            self._emit_status(
                f"max-missions cap reached ({self.config.budget.max_missions})"
            )
            return {"status": "iteration_cap", "item_id": item.id}

        return self._run_one(item)

    # ------------------------------------------------------------------
    # One mission
    # ------------------------------------------------------------------

    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        prelude = self.memory.render_prelude(objective=item.objective)
        self.memory.backlog.mark_running(item.id)
        self._missions_started += 1

        self._emit({
            "type": "life.mission.started",
            "item_id": item.id,
            "title": item.title,
            "missions_started": self._missions_started,
        })

        cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
        )

        outcome: Any = None
        exc_str: str | None = None
        t0 = time.time()
        try:
            outcome = self.runner.execute(
                objective=item.objective,
                sink=cost_sink,
                prelude_context=prelude,
            )
        except Exception as exc:  # noqa: BLE001
            exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
        elapsed = time.time() - t0

        success = bool(getattr(outcome, "success", False)) if outcome else False
        status = str(getattr(outcome, "status", "error") if outcome else "error")
        rounds = int(getattr(outcome, "rounds", 0) or 0)
        stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        usd = cost_sink.total_usd()

        # Update backlog row.
        if success:
            self.memory.backlog.mark_done(item.id)
        else:
            err = exc_str or stop_reason or "unspecified failure"
            self.memory.backlog.mark_failed(item.id, error=err)

        # Journal entry.
        kind = "mission_complete" if success else "mission_failed"
        summary_parts = [
            f"status={status}",
            f"rounds={rounds}",
            f"elapsed={elapsed:.1f}s",
            f"tokens_in={cost_sink.total_input_tokens()}",
            f"tokens_out={cost_sink.total_output_tokens()}",
            f"cost_usd=${usd:.4f}",
        ]
        if stop_reason:
            summary_parts.append(f"reason={stop_reason}")
        if exc_str:
            summary_parts.append(f"exc={exc_str}")
        entry = JournalEntry.new(
            kind=kind,
            title=item.title,
            summary="; ".join(summary_parts),
            tags=list(item.tags) + ["life"],
            cost_usd=usd,
            extra={
                "item_id": item.id,
                "objective": item.objective,
                "engineer_model": self.engineer_model,
                "reviewer_model": self.reviewer_model,
                "input_tokens": cost_sink.total_input_tokens(),
                "output_tokens": cost_sink.total_output_tokens(),
                "matched_skill": str(getattr(outcome, "matched_skill_name", "") or ""),
                "skill_distilled": bool(getattr(outcome, "skill_distilled", False)),
                "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
            },
        )
        self.memory.journal.append(entry)

        self._emit({
            "type": "life.mission.completed",
            "item_id": item.id,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
        })

        return {
            "item_id": item.id,
            "title": item.title,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _maybe_stop(self) -> str:
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return "stop_event signalled"
        if self._missions_started >= self.config.budget.max_missions:
            return f"max-missions cap reached ({self.config.budget.max_missions})"
        if self.config.budget.remaining_today(self.memory.journal) <= 0:
            return "daily budget exhausted"
        return ""

    def _wait_idle(self) -> bool:
        """Sleep ``poll_interval_seconds`` honouring stop_event.

        Returns True if stop_event fired during the wait."""
        ev = self.config.stop_event
        if ev is None:
            time.sleep(self.config.poll_interval_seconds)
            return False
        return ev.wait(self.config.poll_interval_seconds)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: event sink raised")

    def _emit_status(self, text: str) -> None:
        self._emit({"type": "life.status", "text": text})
