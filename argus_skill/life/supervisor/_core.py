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
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.ports import EventSink
from ...core.pricing import price_for, usd_for_tokens
from ...core.usage import project_usage_summary
from ..memory import BacklogItem
from ._config import (
    LifeSupervisorConfig,
    _MemoryView,
    _MissionRunner,
    reserve_global_daily_budget,
)
from ._constants import (
    FULL_PAPER_GATE_DESCRIPTION as _FULL_PAPER_GATE_DESCRIPTION,  # noqa: F401
)
from ._constants import (
    IDLE_BACKOFF_BASE_SECONDS as _IDLE_BACKOFF_BASE_SECONDS,  # noqa: F401
)
from ._constants import (
    IDLE_BACKOFF_CAP_SECONDS as _IDLE_BACKOFF_CAP_SECONDS,  # noqa: F401
)
from ._constants import (
    LIFECYCLE_BLOCK_HEARTBEAT_SECONDS as _LIFECYCLE_BLOCK_HEARTBEAT_SECONDS,  # noqa: F401
)
from ._constants import (
    PLAN_AWAITING as _PLAN_AWAITING,
)
from ._constants import (
    PLAN_TERMINAL_IDLE as _PLAN_TERMINAL_IDLE,
)
from ._constants import (
    PLANNER_SCOPE_BOUNDED as _PLANNER_SCOPE_BOUNDED,  # noqa: F401
)
from ._constants import (
    PLANNER_SCOPE_FINAL_SUBMISSION as _PLANNER_SCOPE_FINAL_SUBMISSION,
)
from ._constants import (
    STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS as _STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS,  # noqa: F401
)
from ._constants import (
    VERIFICATION_PROBE_AFTER_IDLE_CYCLES as _VERIFICATION_PROBE_AFTER_IDLE_CYCLES,  # noqa: F401
)
from ._cost import copilot_usd_for_premium_requests
from ._evolution import EvolutionMixin
from ._helpers import (
    _entry_task_signature,
    _planner_task_signature,
    _resolve_task_dep_ids,
    _sanitize_planner_task_text,
)
from ._idle_cycle import IdleCycleMixin, _idle_exit_seconds  # noqa: F401
from ._lifecycle import LifecycleMixin
from ._mission_execution import MissionExecutionMixin
from ._planner_orchestration import PlannerOrchestrationMixin
from ._planner_rendering import PlannerRenderingMixin
from ._planning_context import PlanningContextMixin

log = logging.getLogger(__name__)

_price_for = price_for





# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------




_PLANNER_DEDUP_STATUSES = {"pending", "running", "done"}
# Compatibility constants re-exported from ``life.supervisor``.
_PLANNER_RECENT_HISTORY_WINDOW = 20
# Compatibility export retained for callers/tests that classify journal failures.
_PLANNER_RECENT_FAILURE_STATUS = "no_progress"
# Plan-cycle outcome sentinels returned by ``_plan_next_work`` and consumed
# by ``run()``. Kept as a small named set (not bare string literals scattered
# across call sites) so the control flow stays auditable.
_PLAN_TASKS_ADDED = "tasks_added"
_PLAN_PROJECT_DONE = "project_done"
_PLAN_RETRY = "planner_retry"
_PLAN_HANDOFF = "daemon_handoff"
_PLAN_ERROR = "planner_error"
_PLAN_MANAGER_ROLLBACK = "manager_blocked_rollback"

# Idle backoff for the "no new work" outcomes (awaiting-external / planner
# retry / planner error). Each consecutive idle plan-cycle doubles the host's
# re-check sleep, capped — so a project correctly waiting on a live external
# job (or a planner that keeps finding nothing) is polled every few minutes,
# not continuously. Reset to 0 the moment real work runs.

# Legacy heartbeat used by budget pauses and tests that exercise the old idle
# gate. Planner waiting/idling is now represented by structured events.

# Stall escalation: after this many consecutive idle planner cycles concluding the
# same external dependency blocks progress, dispatch ONE domain-agnostic
# verification-probe mission so the agent TESTS its (possibly stale) belief against
# CURRENT reality instead of waiting forever on a memory of the blocker. Rate-limit
# repeat probes with the cooldown below.

# Operator escalation: after this many consecutive missions that COMPLETED but the
# L2 reviewer judged forward_progress=false (work happened, the goal did NOT
# advance — e.g. repeated no-score / blocked-archive refuges), surface a loud,
# operator-notified stall alert. This counts ONLY the reviewer's own signal — the
# harness never decides what "progress" is; it just refuses to let the agent
# system loop invisibly without bringing the human in.






# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------



# ----- thin protocol describing what we need from a MissionExecutor --------


class LifeSupervisor(
    EvolutionMixin,
    IdleCycleMixin,
    MissionExecutionMixin,
    LifecycleMixin,
    PlanningContextMixin,
    PlannerOrchestrationMixin,
    PlannerRenderingMixin,
):
    """Cross-mission scheduler.

    Public API:

    - :meth:`run` — drive missions until backlog is exhausted, the
      iteration cap is hit, the budget is tripped, or ``stop_event``
      is set. Returns a summary dict (mission count, costs, statuses).

    - :meth:`tick` — process a single backlog item if available; useful
      for tests and CLI ``life next``.

    Memory wiring:

    - Before each mission, we render recent project memory with
      ``LifeMemory.render_prelude()`` and forward it as ``prelude_context``.
    - After each mission, we emit a ``life.mission.completed`` event so the
      next mission can recall it from the event-backed history.
    """

    def __init__(
        self,
        *,
        memory: _MemoryView,
        runner: _MissionRunner,
        sink: EventSink,
        config: LifeSupervisorConfig | None = None,
        engineer_model: str = "gpt-5.5",
        reviewer_model: str = "gpt-5.5",
        planner_runner: Any | None = None,
        skill_store: Any | None = None,
    ) -> None:
        self.memory = memory
        self.runner = runner
        self.sink = sink
        self.config = config or LifeSupervisorConfig()
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        # planner_runner: any RunnerBackend (codex / memory). When None
        # the iteration loop is effectively disabled — items still go
        # ``done`` after the first successful mission. Wired by the
        # life worker / REPL to the same backend the engineer uses.
        self.planner_runner = planner_runner
        # Optional role-scoped skill store for the planner mission matcher.
        # Threaded from the composition root (REPL / life worker). None keeps
        # the planner on fixed role context only (no planner skill pool today).
        self.skill_store = skill_store
        self._missions_started = 0
        self._planning_cycles = 0
        # One-shot guard: the pipeline mode (paper vs optimize) is classified
        # from the continuous objective and persisted exactly once, on the
        # first planner cycle. Set True the moment we attempt resolution so we
        # never re-classify mid-mission.
        self._vertical_resolved = False
        # Idle backoff state (await-external / repeated no-work planner cycles).
        # Persists across daemon outer-loop iterations (the supervisor instance
        # is reused) so backoff escalates while the project waits, and resets
        # the moment a real mission runs.
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        # Wall-clock (monotonic) of the first idle pass in the current idle
        # streak — set by `_enter_idle_backoff`, cleared by `_reset_idle_backoff`
        # — so `_maybe_idle_timeout` can auto-exit a long-idle continuous daemon.
        # Spans the daemon outer-loop sleeps because the supervisor is reused.
        self._idle_since: float | None = None
        self._last_open_ended_project_done_signature = ""
        # Lifecycle-block log-hygiene state: suppress identical held-state
        # emits except on change or a slow heartbeat.
        self._last_lifecycle_block_sig: tuple[str, str] | None = None
        self._last_lifecycle_block_at = 0.0
        # Planner idle/waiting log-hygiene + stall-escalation state (same family
        # as the lifecycle-block heartbeat above): suppress repeated identical
        # planner_waiting/planner_idle events, and rate-limit the
        # verification-probe stall-breaker.
        self._last_planner_idle_sig: str | None = None
        self._last_planner_idle_at = 0.0
        self._last_verification_probe_at = 0.0
        # Consecutive missions that COMPLETED with the reviewer judging
        # forward_progress=false; when it crosses the threshold the harness
        # escalates to the operator (surface, don't loop invisibly).
        self._consecutive_no_progress_missions = 0
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
            self._emit({
                "type": (
                    "life.mission.requeued" if requeued else "life.mission.orphaned"
                ),
                "item_id": it.id,
                "title": it.title,
                "started_ts": it.started_ts,
                "error": it.last_error,
                "orphan_retries": it.orphan_retries,
            })

    @staticmethod
    def _safe_mode_enabled() -> bool:
        return os.environ.get("ARGUS_SKILL_SAFE_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _configured_worktree(self) -> Path | None:
        configured = getattr(self.config, "project_worktree", None)
        if configured is not None:
            return Path(configured).expanduser()
        memory_worktree = getattr(self.memory, "project_worktree", None)
        if memory_worktree is not None:
            return Path(memory_worktree).expanduser()
        return None

    def _project_workdir(self) -> Path:
        configured = self._configured_worktree()
        if configured is not None:
            return configured
        env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
        if env_workdir:
            return Path(env_workdir).expanduser()
        project_root = getattr(self.memory, "project_root", None)
        if project_root:
            return Path(project_root)
        project = getattr(self.memory, "project", None)
        if project is not None:
            root = getattr(project, "root", None)
            if root:
                return Path(root)
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return Path.cwd()

    def _artifact_root(self) -> Path:
        configured = getattr(self.config, "artifact_root", None)
        if configured is not None:
            return Path(configured).expanduser()
        telemetry_dir = getattr(self.config, "telemetry_dir", None)
        if telemetry_dir is not None:
            return Path(telemetry_dir).expanduser()
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return self._project_workdir()

    def _current_pipeline_stage(self) -> str | None:
        """Read current stage through the active vertical contract.

        Do not trust raw ``PIPELINE_STATE.current_stage`` blindly: a project can
        carry ``vertical=kernelbench`` with a stale paper stage like
        ``research``. The stage-checklist helper clamps that to the vertical's
        first valid stage (``setup`` for kernelbench/speedrun).
        """
        try:
            root = self._artifact_root()
            from ...skills.stage_checklists import current_stage

            return current_stage(root)
        except Exception:  # noqa: BLE001
            return None

    def _planner_workdir(self) -> Path:
        configured = self._configured_worktree()
        if configured is not None:
            return configured
        env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
        if env_workdir:
            return Path(env_workdir).expanduser()
        project_root = getattr(self.memory, "project_root", None)
        if project_root:
            return Path(project_root)
        project = getattr(self.memory, "project", None)
        if project is not None:
            root = getattr(project, "root", None)
            if root:
                return Path(root)
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return Path.cwd()

    def _planner_config(self):
        from ...core.knobs import resolve_role_model
        from ...planner import PlannerConfig

        safe_mode = self._safe_mode_enabled()
        return PlannerConfig(
            model=resolve_role_model("planner", role_env="ARGUS_SKILL_PLAN_MODEL")
            or self.reviewer_model,
            reasoning_effort=os.environ.get(
                "ARGUS_SKILL_PLANNER_REASONING_EFFORT", "xhigh"
            ),
            working_dir=str(self._planner_workdir()),
            skip_git_repo_check=True,
            full_auto=safe_mode,
            dangerous_yolo=not safe_mode,
        )

    def _consume_manager_blocked_rollback_before_planner(self) -> dict[str, Any] | None:
        """Consume a current Manager-blocked rollback packet before planning.

        ``Manager.decide_stage_transition(review=None)`` already validates
        ``research/STAGE_CHECK_MANAGER_BLOCKED.json`` and writes rollback only
        when the packet is current, internally consistent, and targets an
        earlier stage. This supervisor hook narrows when it is called: the
        continuous daemon has no backlog item to run and is about to ask the
        planner for more work. A valid packet must win that race; stale or
        mismatched packets fall through as a no-op.
        """
        try:
            from ...manager import Manager

            root = self._artifact_root()
            st = Manager(project_root=root, runner=None).decide_stage_transition(
                review=None,
                project_root=root,
            )
        except Exception:  # noqa: BLE001
            log.debug("pre-planner manager rollback check skipped", exc_info=True)
            return None
        if st.action != "rollback":
            return None
        decision = {
            "action": st.action,
            "target_stage": st.target_stage,
            "reason": st.reason,
            "current_stage": st.current_stage,
            "source": st.source,
            "diagnostic": st.diagnostic,
        }
        self._emit({"type": EventType.LIFE_MANAGER_STAGE_DECISION, **decision})
        self._emit_status(
            "manager consumed rollback-accepted stage-check packet; "
            f"rolled back to {st.target_stage}"
        )
        return decision

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
            # Idle auto-exit: a continuous daemon that has had no real work for
            # longer than the cap exits cleanly so its slot is freed (the
            # session model respawns it on `--resume`). `_idle_since` carries
            # across the daemon's outer-loop sleeps, so this fires once the
            # cumulative idle streak — not any single pass — crosses the window.
            idle_stop = self._maybe_idle_timeout()
            if idle_stop:
                idle_s = round(time.monotonic() - (self._idle_since or 0.0), 1)
                self._emit({
                    "type": EventType.LIFE_DAEMON_IDLE_TIMEOUT,
                    "idle_seconds": idle_s,
                    "agent_layer": "planner",
                })
                self._emit_status(
                    f"idle {idle_s:.0f}s with no work — daemon exiting "
                    f"(resume to continue)"
                )
                stopped_by = idle_stop
                break
            # Early auto-stop: if this is an EMNLP project and the gate
            # already passes, stop immediately — don't run any more ticks
            # or planner cycles.  This prevents the planner from inventing
            # new work (lint, refactor, etc.) after the paper is done.
            if (
                self.config.continuous
                and self.config.continuous_objective
                and self._effective_full_paper_gate(self._artifact_root())
                and self._journal_has_full_paper_gate_success()
            ):
                self._emit_status(
                    "auto-stop: EMNLP gate passes, project complete"
                )
                stopped_by = "project_done"
                break
            try:
                outcome = self.tick()
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                log.exception("life supervisor: tick raised")
                recovered = self._fail_running_items_after_supervisor_error(err)
                self._emit({
                    "type": "life.supervisor.error",
                    "error": err,
                    "recovered_item_ids": recovered,
                })
                results.append({
                    "success": False,
                    "status": "supervisor_error",
                    "reason": err,
                    "recovered_item_ids": recovered,
                })
                stopped_by = "supervisor_error"
                break
            if outcome is None:
                # Backlog empty — continuous mode: ask planner for more
                if self.config.continuous and self.config.continuous_objective:
                    manager_rollback = (
                        self._consume_manager_blocked_rollback_before_planner()
                    )
                    if manager_rollback is not None:
                        stopped_by = _PLAN_MANAGER_ROLLBACK
                        break
                    gate_reason = self._planner_cycle_gate_reason()
                    if gate_reason:
                        self._emit({
                            "type": "life.planner.deferred",
                            "reason": gate_reason,
                            "agent_layer": "planner",
                        })
                        self._emit_status(gate_reason)
                        stopped_by = gate_reason
                        break
                    # Auto-stop: if the EMNLP gate already passes, the
                    # project is done — don't ask the planner to invent
                    # more work.
                    if (
                        self.config.full_paper_gate
                        and self._journal_has_full_paper_gate_success()
                    ):
                        self._emit_status(
                            "planner: project done — EMNLP gate passes"
                        )
                        stopped_by = "project_done"
                        break
                    planned = self._plan_next_work()
                    if planned == "daemon_handoff":
                        stopped_by = "daemon_handoff"
                        break
                    if planned == "planner_retry":
                        stopped_by = "planner_retry"
                        break
                    if planned == _PLAN_AWAITING:
                        # Planner intentionally idled awaiting an external job.
                        # Return cleanly with a suggested backoff so the daemon
                        # outer loop sleeps (escalating) before re-checking,
                        # instead of make-work or a tight re-plan spin.
                        stopped_by = _PLAN_AWAITING
                        break
                    if planned == _PLAN_TERMINAL_IDLE:
                        stopped_by = _PLAN_TERMINAL_IDLE
                        break
                    if planned is True:
                        continue  # new items in backlog, loop around
                    if planned is False:
                        self._emit_status("planner: project done")
                        stopped_by = "project_done"
                        break
                    stopped_by = "planner_error"
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
            if outcome.get("status") in {
                "budget_pause",
                "iteration_cap",
                "lifecycle_block",
            }:
                # No mission actually ran — this is a held/paused outcome. Escalate
                # the wait like the idle path (15→300s) instead of resetting to
                # poll_interval, so a budget pause / F5 hold doesn't busy-spin and
                # re-flood the journal every 5s until the daily cap rolls over.
                self._enter_idle_backoff()
            else:
                # A real mission ran: clear any accumulated no-work backoff.
                self._reset_idle_backoff()
            # Auth failure flagged by _run_one: propagate immediately
            if outcome.get("auth_failure"):
                stopped_by = "auth_failure"
                break
            manager_rollback = (
                self._consume_manager_blocked_rollback_before_planner()
            )
            if manager_rollback is not None:
                stopped_by = _PLAN_MANAGER_ROLLBACK
                break
            post_mission_stop = self._post_mission_hook(outcome)
            if post_mission_stop:
                self._emit({
                    "type": "life.post_mission.stop",
                    "reason": post_mission_stop,
                    "item_id": outcome.get("item_id"),
                    "status": outcome.get("status"),
                })
                self._emit_status(post_mission_stop)
                stopped_by = post_mission_stop
                break
            # Stop conditions that ``tick`` signals via the result dict
            # (budget pause leaves the item PENDING on purpose so a
            # later supervisor run can retry — but for THIS run we must
            # not spin on the same blocked item).  ``lifecycle_block`` is
            # the same shape: the F5 gate leaves the item PENDING and
            # asks for human resume/archive, so we must break out instead
            # of re-ticking the same held item every loop (which would
            # busy-spin ``infer_observable_status`` at 100% CPU). The
            # daemon's outer loop re-enters after ``poll_interval``.
            if outcome.get("status") in {
                "budget_pause",
                "iteration_cap",
                "lifecycle_block",
            }:
                stopped_by = outcome.get("status", "")
                break
        project_usage = project_usage_summary(
            Path(
                getattr(self.memory, "project_root", None)
                or getattr(self.memory, "root", None)
                or self._artifact_root()
            )
        )
        return {
            "missions_started": self._missions_started,
            "missions_run": len(results),
            "planning_cycles": self._planning_cycles,
            "results": results,
            "total_cost_usd": project_usage.cost_usd,
            "known_cost_usd": project_usage.known_cost_usd,
            "pricing_status": project_usage.pricing_status,
            "stopped_by": stopped_by,
            "suggested_sleep": self._suggested_sleep_s,
        }

    def _fail_running_items_after_supervisor_error(self, error: str) -> list[str]:
        """Best-effort cleanup when an unexpected supervisor error escapes.

        ``_run_one`` normally finalizes its claimed item, but this guard
        prevents a bug outside that narrow try/except from leaving durable
        ``running`` rows forever.
        """
        try:
            items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog after error")
            return []

        recovered: list[str] = []
        for item in items:
            if getattr(item, "status", "") != "running":
                continue
            item_id = str(getattr(item, "id", "") or "")
            if not item_id:
                continue
            title = str(getattr(item, "title", "") or "running mission")
            objective = str(getattr(item, "objective", "") or "")
            failure_reason = f"supervisor error: {error}"
            try:
                self.memory.backlog.mark_failed(item_id, error=failure_reason)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to mark running item failed: %s", item_id)
                continue
            recovered.append(item_id)
            usage = project_usage_summary(
                Path(
                    getattr(self.memory, "project_root", None)
                    or getattr(self.memory, "root", None)
                    or self._artifact_root()
                ),
                mission_id=item_id,
            )
            self._emit({
                "type": EventType.LIFE_MISSION_COMPLETED,
                "item_id": item_id,
                "title": title,
                "objective": objective,
                "success": False,
                "status": "supervisor_error",
                "rounds": 0,
                "cost_usd": usage.cost_usd,
                "known_cost_usd": usage.known_cost_usd,
                "pricing_status": usage.pricing_status,
                "usage_record_count": usage.call_count,
                "terminal_status": "supervisor_error",
                "failure_reason": failure_reason,
                "agent_layer": "supervisor",
            })
        return recovered

    def tick(self) -> dict[str, Any] | None:
        """Process at most one backlog item. Returns its result dict or
        ``None`` if nothing was eligible to run."""
        item = self.memory.backlog.next_pending()
        if item is None:
            return None

        obsolete_final_submission = (
            self._maybe_skip_inapplicable_final_submission_item(item)
        )
        if obsolete_final_submission is not None:
            return obsolete_final_submission

        memory_global_root = getattr(self.memory, "global_root", None)
        budget_global_root = (
            Path(memory_global_root) if memory_global_root is not None else None
        )
        ok, reason = self.config.budget.can_start(
            item=item,
            journal=self.memory.journal,
            global_root=budget_global_root,
        )
        if not ok:
            # Don't fail the item — it'll be retried next supervisor
            # run when the daily cap rolls over. Emit a heartbeat-gated event
            # so a long budget pause cannot flood the timeline.
            self._emit_status(f"budget block: {reason}")
            if self._should_journal_idle_repeat("budget_pause"):
                self._emit({
                    "type": EventType.LIFE_BUDGET_PAUSE,
                    "item_id": item.id,
                    "title": item.title,
                    "reason": reason,
                    "agent_layer": "supervisor",
                })
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

        # F5 project-lifecycle gate. Recompute observable status from the
        # project tree, overlay any persisted state (e.g. user quarantine),
        # then ask the policy engine if a transition is warranted. If the
        # resulting state is non-allocatable (quarantined/done/archived),
        # skip this tick — no token budget is spent and the user must
        # explicitly resume / archive.
        lifecycle_block = self._maybe_block_on_lifecycle(item)
        if lifecycle_block is not None:
            return lifecycle_block

        reservation, reserve_reason = reserve_global_daily_budget(
            cap_usd=self.config.budget.global_daily_cap_usd,
            amount_usd=self._effective_per_mission_cap(item),
            global_root=budget_global_root,
            owner=item.id,
        )
        if reservation is None:
            self._emit_status(f"budget block: {reserve_reason}")
            self._emit({
                "type": EventType.LIFE_BUDGET_PAUSE,
                "item_id": item.id,
                "title": item.title,
                "reason": reserve_reason,
                "agent_layer": "supervisor",
            })
            return {
                "status": "budget_pause",
                "item_id": item.id,
                "reason": reserve_reason,
            }
        try:
            return self._run_one(item)
        finally:
            reservation.release()

    def _maybe_skip_inapplicable_final_submission_item(
        self,
        item: BacklogItem,
    ) -> dict[str, Any] | None:
        """Retire stale paper-final tasks when the active vertical is bounded.

        ``scope:final_submission`` only has meaning when the active vertical's
        completion gate is ``full_paper``. If a stale default ``research`` state
        caused the planner to enqueue a final-submission proof for a
        Manager-authored bounded domain (for example ``perf_tuning``), do not
        spend another engineer/reviewer round proving the paper pipeline is
        missing. Mark the planner artifact ``skipped`` and let the bounded
        project reach its own terminal planner verdict.
        """
        if self._planner_scope_from_item(item) != _PLANNER_SCOPE_FINAL_SUBMISSION:
            return None
        if self._effective_full_paper_gate(self._artifact_root()):
            return None

        reason = (
            "skipped stale final_submission task: active vertical completion "
            "gate is not full_paper"
        )
        self.memory.backlog.update(
            item.id,
            status="skipped",
            finished_ts=time.time(),
            last_error=reason,
        )
        self._emit({
            "type": "life.planner.final_submission_skipped",
            "item_id": item.id,
            "title": item.title,
            "reason": reason,
            "agent_layer": "supervisor",
        })
        self._emit_status(reason)
        return {"status": "skipped", "item_id": item.id, "reason": reason}

    # ------------------------------------------------------------------
    # One mission
    # ------------------------------------------------------------------

    def _effective_per_mission_cap(self, item: BacklogItem) -> float:
        """The cap enforced for ``item`` (min of operator per-item budget and the
        global per-mission cap). Delegates to ``LifeBudget`` so the preflight
        ``can_start`` check and the mid-mission breaker use one number (F3)."""
        return self.config.budget.effective_per_mission_cap(item)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: event sink raised")

    def _emit_status(self, text: str) -> None:
        self._emit({"type": "life.status", "text": text})

    def _resolve_vertical_once(self) -> None:
        """DECIDE + persist the active vertical exactly once per mission, BEFORE
        any gate/stage read (``resolve_vertical``) runs.

        Precedence:
        * An already-persisted vertical is TRUSTED and re-persisted (sticky
          across daemon restarts; a chosen per-task vertical stays chosen).
        * Otherwise, when a continuous objective is set, the MANAGER AGENT
          decides it (``Manager.divide`` — one grounded call, no keyword
          classifier) and persists it (autonomously authoring a new data domain
          when no built-in fits).

        FAIL-HARD: an undecidable vertical, a missing backend, or a corrupt
        ``PIPELINE_STATE.json`` PROPAGATES — a mission that cannot determine its
        vertical must fail loudly, not silently run the research pipeline.
        """
        if getattr(self, "_vertical_resolved", False):
            return
        # Flip the guard immediately so this decision runs exactly once.
        self._vertical_resolved = True

        from ...skills import vertical_select as _vsel

        artifact_root = self._artifact_root()
        persisted = _vsel._persisted_vertical(artifact_root)
        if persisted is not None:
            # Trust the persisted vertical and re-persist it (sticky). Does NOT
            # touch current_stage — stage authority is the reviewer agent's.
            _vsel.persist_vertical(artifact_root, persisted)
            self._emit({
                "type": "life.vertical.resolved",
                "vertical": persisted,
                "profile_hint": "persisted",
                "agent_layer": "planner",
            })
            return

        if not self.config.continuous_objective:
            return

        from ...manager import Manager

        mgr = Manager(project_root=artifact_root, runner=self.planner_runner)
        division = mgr.divide(self.config.continuous_objective)
        self._emit({
            "type": "life.vertical.resolved",
            "vertical": division.vertical,
            "profile_hint": "manager",
            "agent_layer": "planner",
        })
        log.info(
            "life supervisor: resolved vertical = %s (manager-decided)",
            division.vertical,
        )

    def _plan_next_work(self) -> bool | None | str:
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and
        ``"daemon_handoff"`` if the planner asked the host to restart,
        and ``None`` when the planner fails and should be retried later.
        """
        terminal_idle = self._maybe_idle_after_unchanged_open_ended_done()
        if terminal_idle is not None:
            return terminal_idle

        self._planning_cycles += 1
        manager_intent = self._manager_intent_context()
        self._emit({
            "type": EventType.LIFE_PLANNER_START,
            "cycle": self._planning_cycles,
            "objective": self.config.continuous_objective[:200],
            "manager_intent": manager_intent,
        })

        wiki_collect_task = self._wiki_collect_task_if_due_under_blocker()
        if wiki_collect_task is not None:
            return self._enqueue_wiki_collect_task(wiki_collect_task)

        if self.planner_runner is None:
            self._emit_status("planner error: no planner runner wired; retry later")
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "no planner runner wired",
            })
            return None

        # Only skip the planner on an operator-only external blocker when the
        # full EMNLP gate is active. A ``--bounded`` mission
        # (``full_paper_gate=False``) does not require the external benchmark
        # targets, so it must fall through to the planner and reach its own
        # ``project_done`` instead of waiting forever on artifacts it never
        # needs. Mirrors the gating in
        # ``_defer_project_done_for_operator_external_blocker``.
        short_circuit = None
        if self._effective_full_paper_gate(self._artifact_root()):
            short_circuit = self._operator_external_blocker_short_circuit_decision(
                project_root=self._project_workdir(),
            )
        if short_circuit is not None:
            return self._record_planner_waiting(
                short_circuit,
                planner_cost_usd=0.0,
            )

        # The mission is now committing to real planning work — every idle /
        # blocked / no-runner / done short-circuit above has returned. Decide +
        # persist the vertical here (once, guarded), so the planner and its
        # downstream gate reads see a stable vertical. Placing it AFTER the
        # short-circuits means a blocked/idle cycle never triggers a Manager
        # decision (nor a wasted planner-runner call).
        self._resolve_vertical_once()

        journal_tail = self._render_journal_for_planner()

        runtime_note = self._planner_runtime_with_idle_note()

        subagent_family_failures = self._recent_subagent_family_failures()
        stuck_families_note = self._stuck_subagent_families_note(subagent_family_failures)

        try:
            from ...planner import Planner

            planner = Planner(self.planner_runner, skill_store=self.skill_store)
            # Enable streaming so planner output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                verdict = planner.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,

                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary="\n\n".join(
                        part for part in (
                            self._manager_intent_prompt_block(manager_intent),
                            stuck_families_note,
                            runtime_note,
                        ) if part
                    ),
                    config=self._planner_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": f"{type(exc).__name__}: {exc}",
            })
            return None

        planner_cost_usd = usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.cached_input_tokens,
            verdict.output_tokens,
            reasoning_output_tokens=verdict.reasoning_output_tokens,
            price_lookup=price_for,
        ) + copilot_usd_for_premium_requests(verdict.premium_requests)

        if verdict.error:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": verdict.error,
                "raw_text": verdict.raw_text,
            })
            self._emit_status(f"planner error: {verdict.error}; retry later")
            # A planner error is a no-work outcome: back off before retrying so
            # a persistently-failing planner cannot spin every poll interval.
            self._enter_idle_backoff()
            return _PLAN_ERROR

        verdict = self._defer_project_done_for_operator_external_blocker(verdict)

        # First-class await-external: the planner intentionally idled because
        # the project is blocked on a live, nonterminal external job and there
        # is no new high-impact work. NOT an error, NOT make-work — record a
        # lightweight waiting entry and back off (escalating) before re-checking.
        if verdict.waiting:
            record = self._record_planner_waiting(
                verdict,
                planner_cost_usd=planner_cost_usd,
            )
            # Stall-breaker: if the planner has idled K+ cycles on the same
            # blocker, force a verification probe so reality (not a memory of
            # the blocker) drives the next decision. Running it next tick resets
            # the idle backoff via _reset_idle_backoff().
            if self._maybe_dispatch_verification_probe(verdict):
                return True
            return record

        # The planner's tasks are trusted. Deterministic gate-repair is only
        # used as a fallback when the planner itself fails (verdict.error above).

        if (
            verdict.project_done
            and self._effective_full_paper_gate(self._artifact_root())
            and not self._journal_has_full_paper_gate_success()
        ):
            from ...planner import TaskSpec

            verdict = replace(
                verdict,
                project_done=False,
                reason=(
                    "full-pipeline final-submission readiness is required before "
                    "project_done; queueing final submission proof"
                ),
                new_tasks=[
                    TaskSpec(
                        title="Prove final submission readiness",
                        objective=(
                            "Project-final task. Scope: final_submission. "
                            "Complete and verify every item on the full research "
                            "pipeline checklist (research → run → analysis → draft → "
                            "submission). The reviewer will certify completion only "
                            "when EVERY checklist item is satisfied with concrete "
                            "evidence (command output, file contents, query rows). "
                            "Do not declare done based on a single stage, a pilot run, "
                            "or an underlength draft; inspect any unmet checklist item "
                            "and repair experiments, baselines, ablations, paper "
                            "contract, figures, citations, manifest, or submission "
                            "state as needed until the reviewer certifies the project."
                        ),
                        impact_score=5,
                        impact_area="requirement_gap",
                        evidence=(
                            "Planner attempted project_done without an event "
                            "certifying full-pipeline final-submission readiness."
                        ),
                        scope=_PLANNER_SCOPE_FINAL_SUBMISSION,
                    )
                ],
            )

        if verdict.project_done and self.config.open_ended:
            self._last_open_ended_project_done_signature = (
                self._open_ended_terminal_idle_signature()
            )
            self._enter_idle_backoff()
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
                "open_ended_objective": True,
            })
            self._emit_status(
                "planner: project done — continuing later for open-ended objective"
            )
            return _PLAN_RETRY

        if verdict.project_done:
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
            })
            self._emit_status(
                f"planner: project done — {verdict.reason}"
            )
            if verdict.restart_daemon and self._handle_planner_restart(
                verdict.restart_reason
            ):
                self._emit_status("daemon_handoff")
                return "daemon_handoff"
            return False

        if verdict.restart_daemon and not verdict.new_tasks:
            restart_reason = verdict.restart_reason or verdict.reason
            self._emit({
                "type": EventType.LIFE_PLANNER_VERDICT,
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": 0,
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": True,
                "restart_reason": restart_reason,
            })
            if self._handle_planner_restart(restart_reason):
                self._emit_status("daemon_handoff")
                return "daemon_handoff"
            self._emit_status("planner requested daemon restart but host did not restart")
            self._enter_idle_backoff()
            return _PLAN_ERROR

        if not verdict.new_tasks:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": self._planning_cycles,
                "error": "planner produced no tasks",
                "raw_text": verdict.raw_text,
            })
            self._emit_status("planner error: produced no tasks; retry later")
            # No tasks, no waiting flag, not done: a degenerate no-work cycle.
            # Back off so repeated empty plans cannot spin the daemon.
            self._enter_idle_backoff()
            return _PLAN_ERROR

        try:
            existing_items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            existing_items = []

        seen_signatures: dict[tuple[str, str], BacklogItem] = {}
        for existing in existing_items:
            if existing.status not in _PLANNER_DEDUP_STATUSES:
                continue
            if (
                existing.status == "done"
                and self._item_is_final_submission(existing)
            ):
                continue
            signature = _planner_task_signature(existing.title, existing.objective)
            if existing.status in {"pending", "running"}:
                seen_signatures[signature] = existing
            elif signature not in seen_signatures:
                seen_signatures[signature] = existing

        recent_failures = self._recent_no_progress_failures()
        added_titles: list[str] = []
        skipped_duplicate_titles: list[str] = []
        skipped_recent_failure_titles: list[str] = []
        skipped_subagent_family_failure_titles: list[str] = []
        added_impact_scores: list[int] = []

        # Add new tasks to the backlog.
        #
        # Two passes so a planner-emitted DAG can be wired up before anything is
        # enqueued. Pass 1 builds the surviving items (after dedup / recent-
        # failure skips, exactly as before) WITHOUT adding them yet, and records
        # each task's local ``key`` → real ``item.id`` in ``key_map``. Pass 2
        # resolves each task's ``deps`` (local keys → real ids) onto the item and
        # only then adds it. A flat task (no key/deps) flows through with an empty
        # dep list, so its enqueue is byte-for-byte identical to the old path.
        key_map: dict[str, str] = {}
        pending_items: list[tuple[Any, Any]] = []  # (task, item)
        for task in verdict.new_tasks:
            sanitized_title = _sanitize_planner_task_text(task.title)
            sanitized_objective = _sanitize_planner_task_text(task.objective)
            sanitized_evidence = _sanitize_planner_task_text(task.evidence)
            if (
                sanitized_title != task.title
                or sanitized_objective != task.objective
                or sanitized_evidence != task.evidence
            ):
                task = replace(
                    task,
                    title=sanitized_title,
                    objective=sanitized_objective,
                    evidence=sanitized_evidence,
                )
            signature = _planner_task_signature(task.title, task.objective)
            duplicate_item = seen_signatures.get(signature)
            if duplicate_item is not None:
                skipped_duplicate_titles.append(task.title)
                duplicate_reason = (
                    "duplicate completed task"
                    if duplicate_item.status == "done"
                    else "duplicate pending/running task"
                )
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": duplicate_item.id,
                    "matched_status": duplicate_item.status,
                    "reason": duplicate_reason,
                })
                continue
            recent_failure = recent_failures.get(signature)
            if recent_failure is not None:
                skipped_recent_failure_titles.append(task.title)
                failure_extra = getattr(recent_failure, "extra", {}) or {}
                failure_signature = _entry_task_signature(recent_failure)
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": failure_extra.get("item_id"),
                    "matched_title": recent_failure.title,
                    "matched_status": failure_extra.get("terminal_status") or failure_extra.get("status"),
                    "matched_stop_reason": failure_extra.get("stop_reason") or failure_extra.get("failure_reason"),
                    "matched_signature": (
                        {
                            "title": failure_signature[0],
                            "objective": failure_signature[1],
                        }
                        if failure_signature is not None
                        else None
                    ),
                    "skip_category": "recent_no_progress_failure",
                    "reason": "recent no_progress failure",
                })
                continue
            family_failure = next(
                (
                    ff for ff in subagent_family_failures.values()
                    if self._task_mentions_family(task, ff.family)
                ),
                None,
            )
            if family_failure is not None:
                skipped_subagent_family_failure_titles.append(task.title)
                self._emit({
                    "type": EventType.LIFE_PLANNER_TASK_SKIPPED,
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_family": family_failure.family,
                    "matched_streak": family_failure.streak,
                    "matched_last_task_id": family_failure.last_task_id,
                    "matched_last_state": family_failure.last_state,
                    "matched_last_reason": family_failure.last_reason,
                    "skip_category": "recent_subagent_family_failure",
                    "reason": (
                        f"subagent family {family_failure.family!r} has failed "
                        f"{family_failure.streak} times in a row unresolved"
                    ),
                })
                continue
            item = BacklogItem.new(
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=self._planner_task_tags(task),
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                iteration_budget_usd=self._item_iteration_budget(),
            )
            # Reserve the signature now so a later sibling in the SAME batch
            # with an identical title/objective still de-dupes against this
            # one (matches the old single-pass behaviour). The item is not
            # added to the backlog until pass 2.
            seen_signatures[signature] = item
            if getattr(task, "key", ""):
                key_map[task.key] = item.id
            pending_items.append((task, item))

        # Pass 2: resolve local dep keys to real item ids, then enqueue. Only
        # intra-batch deps are supported — a key the planner referenced but did
        # not define in THIS batch (typo, or an unsupported cross-cycle ref) is
        # dropped with a warning so a stray key cannot wedge the item forever.
        for task, item in pending_items:
            task_deps = list(getattr(task, "deps", []) or [])
            if task_deps:
                resolved_ids, unresolved_keys = _resolve_task_dep_ids(
                    task_deps, key_map
                )
                item.deps = resolved_ids
                if unresolved_keys:
                    log.warning(
                        "life supervisor: dropping unresolved planner dep "
                        "key(s) %s for task %r (only same-batch new_tasks deps "
                        "are supported)",
                        unresolved_keys,
                        item.title,
                    )
            self.memory.backlog.add(item)
            added_titles.append(item.title)
            added_impact_scores.append(task.impact_score)
            self._emit({
                "type": EventType.LIFE_PLANNER_TASK_ADDED,
                "item_id": item.id,
                "title": item.title,
                "objective": item.objective,
                "deps": list(item.deps),
                "priority": item.priority,
                "branch_id": item.id,
                "parent_branch_id": item.deps[0] if item.deps else None,
                "impact_score": task.impact_score,
                "impact_area": task.impact_area,
                "manager_intent": manager_intent,
            })

        self._emit({
            "type": EventType.LIFE_PLANNER_VERDICT,
            "cycle": self._planning_cycles,
            "project_done": verdict.project_done,
            "reason": verdict.reason,
            "task_count": len(verdict.new_tasks),
            "enqueued_tasks": len(added_titles),
            "skipped_duplicate_tasks": len(skipped_duplicate_titles),
            "skipped_recent_failure_tasks": len(skipped_recent_failure_titles),
            "skipped_subagent_family_failure_tasks": len(skipped_subagent_family_failure_titles),
            "enqueued_titles": added_titles,
            "enqueued_impact_scores": added_impact_scores,
            "skipped_duplicate_titles": skipped_duplicate_titles,
            "skipped_recent_failure_titles": skipped_recent_failure_titles,
            "skipped_subagent_family_failure_titles": skipped_subagent_family_failure_titles,
            "stuck_subagent_families": {
                family: failure.streak
                for family, failure in subagent_family_failures.items()
            },
            "input_tokens": verdict.input_tokens,
            "cached_input_tokens": verdict.cached_input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": planner_cost_usd,
            "restart_daemon": verdict.restart_daemon,
            "restart_reason": verdict.restart_reason,
            "manager_intent": manager_intent,
        })
        if verdict.restart_daemon and self._handle_planner_restart(
            verdict.restart_reason
        ):
            self._emit_status("daemon_handoff")
            return "daemon_handoff"
        # Real new work was queued: clear the no-work backoff so the next cycle
        # runs promptly.
        self._reset_idle_backoff()
        return True
