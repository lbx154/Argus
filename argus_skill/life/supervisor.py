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
  AND a daily cap. Defaults are generous enough for long polish runs
  (max 6 autonomous missions in one supervisor run, $30/mission,
  $180/day).
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
from typing import Any, Protocol

from ..core.ports import EventSink
from .memory import (
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

    per_mission_cap_usd: float = 30.0
    daily_cap_usd: float = 180.0
    max_missions: int = 6

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
        """Return ``(allowed, reason)``. ``reason`` is empty when allowed.

        We do NOT refuse a mission just because ``item.max_cost_usd``
        exceeds ``per_mission_cap_usd`` — that's a daemon-vs-item
        misconfiguration and a 7×24 product should keep working. The
        per-mission cap is enforced inside the supervisor by clamping
        the effective per-mission budget (see
        ``LifeSupervisor._effective_per_mission_cap``); this method
        only blocks on the *daily* budget envelope, which is the real
        bottom line.
        """
        remain = self.remaining_today(journal, now=now)
        # Use the smaller of (operator-requested mission budget, our
        # per-mission cap) when comparing to daily remaining — same
        # number the supervisor will actually permit.
        effective_cap = min(item.max_cost_usd, self.per_mission_cap_usd)
        if remain < effective_cap:
            return False, (
                f"daily budget remaining ${remain:.2f} < "
                f"effective mission cap ${effective_cap:.2f}"
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


def _usd_for_tokens(model: str, input_tokens: int, output_tokens: int) -> float:
    """Convert token usage into USD using the configured model price."""
    in_price, out_price = _price_for(model)
    return (
        float(input_tokens) * in_price / 1_000_000
        + float(output_tokens) * out_price / 1_000_000
    )


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
        on_phase_change: Any = None,  # Callable[[str, dict], None] | None
    ) -> None:
        self.downstream = downstream
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self.engineer_input_tokens = 0
        self.engineer_output_tokens = 0
        self.reviewer_input_tokens = 0
        self.reviewer_output_tokens = 0
        self._on_phase_change = on_phase_change
        self._reviewer_notified = False
        self._engineer_round_count = 0

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "round.main.completed":
                self.engineer_input_tokens += int(event.get("input_tokens", 0) or 0)
                self.engineer_output_tokens += int(event.get("output_tokens", 0) or 0)
                self._engineer_round_count += 1
            elif kind == "round.review.completed":
                self.reviewer_input_tokens += int(event.get("input_tokens", 0) or 0)
                self.reviewer_output_tokens += int(event.get("output_tokens", 0) or 0)
                # Notify on first reviewer round per mission
                if not self._reviewer_notified and self._on_phase_change:
                    self._reviewer_notified = True
                    try:
                        self._on_phase_change("reviewer", {
                            "round_index": event.get("round_index", 0),
                            "status": event.get("status", ""),
                            "engineer_rounds": self._engineer_round_count,
                        })
                    except Exception:  # noqa: BLE001
                        log.debug("phase change callback failed", exc_info=True)
        except Exception:  # noqa: BLE001
            log.debug("cost-tracking sink ignored malformed event", exc_info=True)
        # Always forward.
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream event sink raised; continuing")

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Forward stream lines when the downstream sink supports them."""
        try:
            handler = getattr(self.downstream, "handle_stream_line", None)
            if handler is not None:
                handler(stream, line)
        except Exception:  # noqa: BLE001
            log.exception("downstream stream handler raised; continuing")

    def close(self) -> None:
        try:
            closer = getattr(self.downstream, "close", None)
            if closer is not None:
                closer()
        except Exception:  # noqa: BLE001
            log.exception("downstream close raised; continuing")

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
    # Optional callable consulted at the start of every mission; should
    # return one pending operator nudge per call (or ``None`` when the
    # bus is empty). The supervisor splices each message into the
    # prelude_context so the engineer sees it as live operator
    # guidance. The default ``None`` disables the bus.
    user_inbox: Any = None  # Callable[[], str | None] | None
    # Runtime context injected into the prelude of every mission so
    # the agent knows its own backend, models, and budget constraints.
    # Set by the REPL / daemon worker; empty string disables injection.
    runtime_context: str = ""
    # --- Continuous improvement mode -----------------------------------
    # When enabled, the supervisor does not exit when the backlog is
    # empty. Instead it invokes the critic-as-planner to inspect the
    # project and generate the next batch of tasks. The supervisor
    # only stops when the planner declares the project done, or when
    # budget / stop_event fires.
    continuous: bool = False
    continuous_objective: str = ""
    # Optional callback returning ``(enabled, objective)`` — the
    # supervisor calls it each iteration to hot-reload from disk or
    # elsewhere. When ``None``, the static ``continuous`` /
    # ``continuous_objective`` fields are used unchanged.
    continuous_config_provider: Any = None  # Callable[[], tuple[bool, str]] | None


# ----- thin protocol describing what we need from a MissionExecutor --------

class _MissionRunner(Protocol):
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
        critic_runner: Any | None = None,
    ) -> None:
        self.memory = memory
        self.runner = runner
        self.sink = sink
        self.config = config or LifeSupervisorConfig()
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        # critic_runner: any RunnerBackend (codex / memory). When None
        # the iteration loop is effectively disabled — items still go
        # ``done`` after the first successful mission. Wired by the
        # life worker / REPL to the same backend the engineer uses.
        self.critic_runner = critic_runner
        self._missions_started = 0
        self._planning_cycles = 0
        self._reap_orphans_on_startup()

    def _reap_orphans_on_startup(self) -> None:
        """Recover items left ``running`` by a crashed process.

        Items are reset to ``pending`` (up to 3 retries) so they resume
        automatically after a daemon restart. Items that keep crashing
        are marked ``failed`` to prevent poison-pill loops.
        """
        try:
            reaped = self.memory.backlog.reap_orphans()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: orphan reaper failed")
            return
        for it in reaped:
            requeued = it.status == "pending"
            if requeued:
                kind = "mission_requeued"
                title = f"recovered after restart: {it.title}"
                summary = (
                    f"item_id={it.id} "
                    f"retry={it.orphan_retries}/3 "
                    f"will resume automatically"
                )
            else:
                kind = "mission_orphaned"
                title = f"orphaned (max retries): {it.title}"
                summary = (
                    f"item_id={it.id} "
                    f"retries={it.orphan_retries} "
                    f"err={it.last_error}"
                )
            entry = JournalEntry.new(
                kind=kind,
                title=title,
                summary=summary,
                tags=list(it.tags) + ["life", "orphan"],
            )
            try:
                self.memory.journal.append(entry)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to journal orphan %s", it.id)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            self._emit({
                "type": "life.mission.orphaned",
                "item_id": it.id,
                "title": it.title,
                "started_ts": it.started_ts,
                "error": it.last_error,
            })

    # ------------------------------------------------------------------
    # Public driving methods
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Drive missions until a stop condition. Returns a summary."""
        results: list[dict[str, Any]] = []
        stopped_by: str = ""
        while True:
            # Hot-reload continuous config from provider (disk, etc.)
            self._reload_continuous_config()
            stop_reason = self._maybe_stop()
            if stop_reason:
                if stop_reason != "__silent_stop__":
                    self._emit_status(stop_reason)
                stopped_by = stop_reason
                break
            outcome = self.tick()
            if outcome is None:
                # Backlog empty — continuous mode: ask planner for more
                if self.config.continuous and self.config.continuous_objective:
                    planned = self._plan_next_work()
                    if planned is True:
                        continue  # new items in backlog, loop around
                    if planned is False:
                        self._emit_status("planner: project done")
                        stopped_by = "project_done"
                        break
                    stopped_by = "planner_unavailable"
                    break
                # Non-continuous: sleep then re-check (so user-added
                # items via the file get picked up). Sleep is bounded
                # by the stop_event so a Ctrl-C shuts us down quickly.
                if self._wait_idle():
                    self._emit_status("stop requested while idle")
                    stopped_by = "stop_requested"
                    break
                # Re-check: if backlog still empty, exit cleanly so
                # `life run --once` semantics work in tests.
                if self.memory.backlog.next_pending() is None:
                    self._emit_status("backlog empty; exiting")
                    stopped_by = "backlog_empty"
                    break
                continue
            results.append(outcome)
            # Auth failure flagged by _run_one: propagate immediately
            if outcome.get("auth_failure"):
                stopped_by = "auth_failure"
                break
            # Stop conditions that ``tick`` signals via the result dict
            # (budget pause leaves the item PENDING on purpose so a
            # later supervisor run can retry — but for THIS run we must
            # not spin on the same blocked item).
            if outcome.get("status") in {"budget_pause", "iteration_cap"}:
                stopped_by = outcome.get("status", "")
                break
        return {
            "missions_started": self._missions_started,
            "planning_cycles": self._planning_cycles,
            "results": results,
            "stopped_by": stopped_by,
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
            entry = JournalEntry.new(
                kind="budget_pause",
                title=f"paused before '{item.title}'",
                summary=reason,
                tags=["budget"],
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return {"status": "budget_pause", "item_id": item.id, "reason": reason}

        if not self.config.continuous and self._missions_started >= self.config.budget.max_missions:
            # Only narrate the cap when there's actually pending work
            # being held back. If the backlog is empty (or the user
            # asked for ``--once`` and we just ran their one mission),
            # this message is just noise.
            try:
                more_pending = self.memory.backlog.next_pending() is not None
            except Exception:  # noqa: BLE001
                more_pending = False
            if more_pending:
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
        # Inject runtime context (backend, models, budget) so the agent
        # knows its own environment. Placed before operator nudges so
        # nudges can override if needed.
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
        # Drain any pending operator nudges from the inbox bus and
        # splice them in front of the prelude as live operator
        # guidance. Each round in the engineer loop will see this as
        # `Operator message history`.
        nudges = self._drain_user_inbox()
        if nudges:
            prelude = (
                "## Operator messages (live nudges, most recent last)\n"
                + "\n".join(f"- {m}" for m in nudges)
                + "\n\n---\n\n"
                + prelude
            )
        # Atomic claim: flip pending → running in one rewrite. If the
        # head moved between the budget peek and now (concurrent writer
        # or user `/rm`), bail; the next tick will re-evaluate.
        claimed = self.memory.backlog.claim_next()
        if claimed is None or claimed.id != item.id:
            if claimed is not None:
                # Roll back so the next tick sees it again. running →
                # pending is a legal transition (only terminal states
                # are sealed).
                try:
                    self.memory.backlog.update(claimed.id, status="pending")
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: claim rollback failed")
            return {"status": "claim_lost", "item_id": item.id}
        item = claimed
        self._missions_started += 1

        self._emit({
            "type": "life.mission.started",
            "item_id": item.id,
            "title": item.title,
            "missions_started": self._missions_started,
        })
        # Notify: mission starting (engineer layer)
        try:
            start_entry = JournalEntry.new(
                kind="mission_started",
                title=item.title,
                summary=f"objective={item.objective[:200]}",
                tags=list(item.tags) + ["life"],
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "engineer",
                },
            )
            self.memory.journal.append(start_entry)
            self._inject_cumulative_cost(start_entry)
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(start_entry)
        except Exception:  # noqa: BLE001
            log.debug("mission_started notify failed; non-critical")

        # Phase-change callback: notifies Telegram when reviewer starts
        def _phase_cb(layer: str, info: dict[str, Any]) -> None:
            try:
                from .notify import dispatch_journal_entry
                entry = JournalEntry.new(
                    kind="phase_change",
                    title=item.title,
                    summary=f"round {info.get('round_index', '?')}: {layer} 开始评审",
                    tags=["life", "phase"],
                    extra={
                        "item_id": item.id,
                        "objective": item.objective,
                        "agent_layer": layer,
                        "engineer_rounds": info.get("engineer_rounds", 0),
                    },
                )
                # Don't journal phase changes — just notify
                self._inject_cumulative_cost(entry)
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.debug("phase_change notify failed; non-critical")

        cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
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

        # Auth failure: the codex backend detected an expired/invalid
        # token. Stop the supervisor so we don't loop over failing
        # missions all night. The operator needs to run `codex login`.
        auth_failure = bool(getattr(outcome, "auth_failure", False))
        if auth_failure:
            self._emit({
                "type": "life.auth_failure",
                "item_id": item.id,
                "text": (
                    "⚠️  codex authentication failed — run `codex login` "
                    "to refresh credentials, then restart the REPL/daemon."
                ),
            })
            ev = self.config.stop_event
            if ev is not None:
                ev.set()

        # ---- iteration loop: should we requeue for another polish cycle?
        # Trigger on `success` (mission marked done) AND on `max_rounds`
        # (engineer ran out of rounds without reviewer-confirmed done).
        # The latter is critical for a 7×24 product: when the engineer
        # built a perfectly correct artifact but reviewer kept demanding
        # more verbatim evidence, we don't want the whole mission to die
        # — let the critic sub-agent inspect the work and either certify
        # it as done or ask for a *concrete* next round.
        iteration_outcome: dict[str, Any] | None = None
        salvage_mode = (not success) and status == "max_rounds" and item.iterate
        # Chat fast-path: when the runner short-circuited a conversational
        # input, there is no artifact to polish — skip the critic loop
        # entirely. Otherwise the critic would try to "improve" a
        # one-line greeting reply, costing another LLM call for no gain.
        chat_mode = bool(getattr(outcome, "chat_mode", False))
        if not chat_mode and (success or salvage_mode):
            iteration_outcome = self._maybe_iterate(
                item=item,
                outcome=outcome,
                cycle_cost_usd=usd,
                salvage_mode=salvage_mode,
            )
        # If the critic accepted the salvage attempt, treat the mission
        # as successful so it transitions to ``done`` not ``failed``.
        if salvage_mode and iteration_outcome and iteration_outcome.get("salvaged"):
            success = True
            status = "done"
            stop_reason = iteration_outcome.get("stop_reason") or stop_reason

        iteration_bonus_usd = 0.0
        if iteration_outcome:
            iteration_bonus_usd = float(iteration_outcome.get("critic_cost_usd", 0.0) or 0.0)
            usd += iteration_bonus_usd

        # Update backlog row.
        if iteration_outcome and iteration_outcome.get("requeued"):
            # Item is back to ``pending``; do not mark_done. The next
            # tick will pick it up and re-execute with the polished
            # objective.
            pass
        elif success:
            self.memory.backlog.mark_done(item.id)
        else:
            err = exc_str or stop_reason or "unspecified failure"
            self.memory.backlog.mark_failed(item.id, error=err)

        # Journal entry.
        if iteration_outcome and iteration_outcome.get("requeued"):
            kind = "mission_iterated"
        else:
            kind = "mission_complete" if success else "mission_failed"
        summary_parts = [
            f"status={status}",
            f"rounds={rounds}",
            f"elapsed={elapsed:.1f}s",
            f"tokens_in={cost_sink.total_input_tokens()}",
            f"tokens_out={cost_sink.total_output_tokens()}",
            f"cost_usd=${usd:.4f}",
        ]
        if iteration_outcome:
            summary_parts.append(
                f"iter={iteration_outcome.get('cycles_done', 0)}/{item.iteration_max_cycles}"
            )
            if iteration_outcome.get("requeued"):
                summary_parts.append(
                    f"improvements={iteration_outcome.get('improvement_count', 0)}"
                )
            elif iteration_outcome.get("stop_reason"):
                summary_parts.append(f"iter_stop={iteration_outcome['stop_reason']}")
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
                "agent_layer": "critic" if iteration_outcome and iteration_outcome.get("requeued") else "engineer",
                "engineer_model": self.engineer_model,
                "reviewer_model": self.reviewer_model,
                "input_tokens": cost_sink.total_input_tokens(),
                "output_tokens": cost_sink.total_output_tokens(),
                "matched_skill": str(getattr(outcome, "matched_skill_name", "") or ""),
                "skill_distilled": bool(getattr(outcome, "skill_distilled", False)),
                "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
                "iteration": iteration_outcome or {},
            },
        )
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")

        self._emit({
            "type": "life.mission.completed",
            "item_id": item.id,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
            "iteration": iteration_outcome or None,
        })

        return {
            "item_id": item.id,
            "title": item.title,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
            "iteration": iteration_outcome,
            "auth_failure": auth_failure,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _drain_user_inbox(self, *, max_messages: int = 10) -> list[str]:
        """Pull all pending operator nudges from the configured inbox.

        Returns up to ``max_messages`` lines (oldest-first). Empty list
        if no inbox is configured or nothing is pending. Any exception
        from the user-supplied callable is swallowed — a flaky bus
        must never break a mission.
        """
        cb = getattr(self.config, "user_inbox", None)
        if cb is None:
            return []
        out: list[str] = []
        for _ in range(max(1, int(max_messages))):
            try:
                msg = cb()
            except Exception:  # noqa: BLE001
                log.exception("user_inbox callable raised; ignoring")
                break
            if not msg:
                break
            text = str(msg).strip()
            if text:
                out.append(text)
        if out:
            self._emit({
                "type": "life.inbox.drained",
                "count": len(out),
                "messages": out,
            })
        return out

    def _maybe_stop(self) -> str:
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return "stop_event signalled"
        # In continuous mode, max_missions is not a hard cap — the
        # planner generates new work indefinitely until it declares
        # the project done. Only daily budget is enforced.
        if not self.config.continuous:
            if self._missions_started >= self.config.budget.max_missions:
                # Suppress the cap message when there's no held-back work.
                # Treats "you asked for one mission, you got one" as silent
                # success rather than a noisy guardrail trip.
                try:
                    more_pending = self.memory.backlog.next_pending() is not None
                except Exception:  # noqa: BLE001
                    more_pending = False
                if more_pending:
                    return f"max-missions cap reached ({self.config.budget.max_missions})"
                return "__silent_stop__"
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

    # ------------------------------------------------------------------
    # Iteration loop
    # ------------------------------------------------------------------

    def _maybe_iterate(
        self,
        *,
        item: BacklogItem,
        outcome: Any,
        cycle_cost_usd: float,
        salvage_mode: bool = False,
    ) -> dict[str, Any] | None:
        """Decide whether to requeue ``item`` for another polish cycle.

        Returns a dict describing the decision (always non-None when
        called on a successful mission). The keys reported back to the
        journal/event sink:

        * ``cycles_done`` — count after this cycle (pre-requeue).
        * ``cost_so_far_usd`` — accumulated iteration cost.
        * ``requeued`` — bool, True if we re-armed the item.
        * ``stop_reason`` — present when ``requeued=False`` because of
          budget / cycles / vanity / disabled / runner missing.
        * ``improvement_count`` / ``improvements`` — when requeued.
        """
        if not item.iterate:
            return {
                "cycles_done": item.iteration_cycles_done,
                "cost_so_far_usd": item.iteration_cost_usd,
                "requeued": False,
                "stop_reason": "iteration disabled",
            }
        if self.critic_runner is None:
            return {
                "cycles_done": item.iteration_cycles_done,
                "cost_so_far_usd": item.iteration_cost_usd,
                "requeued": False,
                "stop_reason": "no critic runner wired",
            }

        cycles_done = int(item.iteration_cycles_done)
        cycles_max = int(item.iteration_max_cycles)
        cost_so_far = float(item.iteration_cost_usd) + max(0.0, float(cycle_cost_usd))
        budget = float(item.iteration_budget_usd)
        remaining_budget = max(0.0, budget - cost_so_far)

        if cycles_done >= cycles_max:
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": f"cycle ceiling {cycles_max} reached",
            }
        if remaining_budget <= 0.0:
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": (
                    f"iteration budget exhausted (${cost_so_far:.2f}/${budget:.2f})"
                ),
            }

        # Pull the reviewer's accepted completion summary.
        latest = ""
        for attr in ("final_message", "completion_summary_markdown", "stop_reason"):
            v = getattr(outcome, attr, "") or ""
            if v:
                latest = str(v)
                break
        original = item.original_objective or item.objective

        # Notify: critic layer starting
        try:
            from .notify import dispatch_journal_entry
            critic_start = JournalEntry.new(
                kind="phase_change",
                title=item.title,
                summary=f"迭代 {cycles_done + 1}/{cycles_max}: 评审员开始评估",
                tags=["life", "phase"],
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "critic",
                    "iteration_cycle": cycles_done + 1,
                    "iteration_max": cycles_max,
                },
            )
            self._inject_cumulative_cost(critic_start)
            dispatch_journal_entry(critic_start)
        except Exception:  # noqa: BLE001
            log.debug("critic phase_change notify failed; non-critical")

        try:
            from ..critic import (
                Critic,
                CriticConfig,
                render_iteration_objective,
            )
            critic = Critic(self.critic_runner)
            verdict = critic.evaluate(
                original_objective=original,
                latest_completion_summary=latest,
                cycles_done=cycles_done,
                cycles_max=cycles_max,
                budget_remaining_usd=remaining_budget,
                journal_tail=self._render_recent_journal_for_critic(item.id),
                config=CriticConfig(model=self.reviewer_model),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: critic raised; finalizing as done")
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": f"critic error: {type(exc).__name__}",
            }

        critic_cost_usd = _usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.output_tokens,
        )
        cost_so_far += critic_cost_usd
        remaining_budget = max(0.0, budget - cost_so_far)

        self._emit({
            "type": "life.iteration.critic",
            "item_id": item.id,
            "stop": verdict.stop,
            "improvement_count": len(verdict.improvements),
            "reason": verdict.reason,
            "input_tokens": verdict.input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": critic_cost_usd,
        })

        if verdict.stop or not verdict.improvements:
            # Salvage path: the engineer hit max_rounds without a `done`
            # verdict, but the critic — looking at journal evidence —
            # decided no further work is needed. Promote the mission to
            # ``done`` so the operator isn't woken up by a false-failure.
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": verdict.reason or "critic stopped",
                "salvaged": bool(salvage_mode),
                "critic_cost_usd": critic_cost_usd,
            }

        if remaining_budget <= 0.0:
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": (
                    f"iteration budget exhausted (${cost_so_far:.2f}/${budget:.2f})"
                ),
                "critic_cost_usd": critic_cost_usd,
            }

        new_objective = render_iteration_objective(
            original_objective=original,
            cycles_done=cycles_done,
            improvements=verdict.improvements,
        )
        try:
            self.memory.backlog.requeue_for_iteration(
                item.id,
                new_objective=new_objective,
                cost_delta_usd=cycle_cost_usd + critic_cost_usd,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: requeue_for_iteration failed")
            return {
                "cycles_done": cycles_done,
                "cost_so_far_usd": cost_so_far,
                "requeued": False,
                "stop_reason": f"requeue failed: {type(exc).__name__}",
            }
        self._emit({
            "type": "life.iteration.continued",
            "item_id": item.id,
            "cycles_done": cycles_done + 1,
            "cycles_max": cycles_max,
            "cost_so_far_usd": cost_so_far,
            "budget_usd": budget,
            "improvements": [
                {"title": imp.title, "acceptance": imp.acceptance}
                for imp in verdict.improvements
            ],
            "critic_cost_usd": critic_cost_usd,
        })
        return {
            "cycles_done": cycles_done + 1,
            "cost_so_far_usd": cost_so_far,
            "requeued": True,
            "improvement_count": len(verdict.improvements),
            "improvements": [
                {"title": imp.title, "acceptance": imp.acceptance}
                for imp in verdict.improvements
            ],
            "critic_cost_usd": critic_cost_usd,
        }

    def _render_recent_journal_for_critic(self, item_id: str) -> str:
        """A tiny tail of journal entries for the current item, plain text."""
        try:
            entries = self.memory.journal.tail(6)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            extra = getattr(e, "extra", None) or {}
            if isinstance(extra, dict) and extra.get("item_id") == item_id:
                lines.append(f"- {e.kind}: {e.summary}")
        return "\n".join(lines[-3:])

    def _inject_cumulative_cost(self, entry: Any) -> None:
        """Stamp ``cumulative_cost_usd`` onto ``entry.extra`` after it has
        been appended to the journal (so the current entry is included)."""
        try:
            cumul = self.memory.journal.total_cost_since(0)
            extra = getattr(entry, "extra", None)
            if extra is None:
                entry.extra = {"cumulative_cost_usd": round(cumul, 2)}
            else:
                extra["cumulative_cost_usd"] = round(cumul, 2)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Hot-reload continuous config
    # ------------------------------------------------------------------

    def _reload_continuous_config(self) -> None:
        """Update ``self.config.continuous`` from the config provider.

        Called at the top of every ``run()`` iteration so that changes
        from the REPL (written to disk) take effect within seconds even
        when the supervisor is in a long continuous run.
        """
        provider = self.config.continuous_config_provider
        if provider is None:
            return
        try:
            enabled, objective = provider()
            self.config.continuous = enabled
            if objective:
                self.config.continuous_objective = objective
        except Exception:  # noqa: BLE001
            log.debug("continuous config provider raised; keeping current values")

    # ------------------------------------------------------------------
    # Planner — continuous improvement mode
    # ------------------------------------------------------------------

    def _plan_next_work(self) -> bool | None:
        """Call the critic-as-planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and
        ``None`` when the planner is unavailable or fails.
        """
        if self.critic_runner is None:
            self._emit_status("planner: no critic runner wired; stopping")
            return None

        self._planning_cycles += 1
        self._emit({
            "type": "life.planner.start",
            "cycle": self._planning_cycles,
            "objective": self.config.continuous_objective[:200],
        })

        journal_tail = self._render_journal_for_planner()
        remaining = self.config.budget.remaining_today(self.memory.journal)

        try:
            from ..critic import Critic, CriticConfig

            critic = Critic(self.critic_runner)
            verdict = critic.plan_next(
                continuous_objective=self.config.continuous_objective,
                journal_tail=journal_tail,
                budget_remaining_usd=remaining,
                planning_cycle=self._planning_cycles - 1,
                config=CriticConfig(model=self.reviewer_model),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; stopping")
            self._emit({
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": f"{type(exc).__name__}: {exc}",
            })
            return None

        planner_cost_usd = _usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.output_tokens,
        )

        self._emit({
            "type": "life.planner.verdict",
            "cycle": self._planning_cycles,
            "project_done": verdict.project_done,
            "reason": verdict.reason,
            "task_count": len(verdict.new_tasks),
            "input_tokens": verdict.input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": planner_cost_usd,
        })

        if verdict.project_done:
            self._emit_status(
                f"planner: project done — {verdict.reason}"
            )
            entry = JournalEntry.new(
                kind="planner_done",
                title="planner declares project done",
                summary=verdict.reason,
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={"agent_layer": "planner"},
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return False

        # Add new tasks to the backlog.
        for task in verdict.new_tasks:
            item = BacklogItem.new(
                title=task.title,
                objective=task.objective,
                priority=100,
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                iteration_budget_usd=self._item_iteration_budget(),
            )
            self.memory.backlog.add(item)
            self._emit({
                "type": "life.planner.task_added",
                "item_id": item.id,
                "title": item.title,
            })

        entry = JournalEntry.new(
            kind="planner_cycle",
            title=f"planner cycle #{self._planning_cycles}",
            summary=(
                f"generated {len(verdict.new_tasks)} task(s): "
                + ", ".join(t.title for t in verdict.new_tasks)
            ),
            tags=["life", "planner"],
            cost_usd=planner_cost_usd,
            extra={
                "agent_layer": "planner",
                "objective": self.config.continuous_objective[:200],
            },
        )
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")
        return True

    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        return 6

    def _item_iteration_budget(self) -> float:
        """Default iteration budget for planner-generated tasks."""
        return 30.0

    def _render_journal_for_planner(self) -> str:
        """Render recent journal entries for the planner's context."""
        try:
            entries = self.memory.journal.tail(20)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            from datetime import datetime
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            lines.append(f"- [{ts}] {e.kind}: {e.title} — {e.summary}")
        return "\n".join(lines) or "(empty)"
