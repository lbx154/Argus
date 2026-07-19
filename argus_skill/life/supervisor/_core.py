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
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.planner_verdict import (
    PlannerVerdictStatus,
    build_planner_verdict_event,
)
from ...core.ports import EventSink
from ...core.pricing import price_for
from ...core.usage import project_usage_summary
from ..memory import BacklogItem
from ..mission_outcome import mission_outcome_class
from ..planner_verdict_outbox import (
    OUTBOX_FILE,
    clear_planner_verdict_outbox,
    load_planner_verdict_outbox,
    mark_planner_verdict_delivered,
    planner_verdict_delivery_id,
    planner_verdict_was_persisted,
    write_planner_verdict_outbox,
)
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
    PLAN_ERROR as _PLAN_ERROR,  # noqa: F401
)
from ._constants import (
    PLAN_HANDOFF as _PLAN_HANDOFF,  # noqa: F401
)
from ._constants import (
    PLAN_RETRY as _PLAN_RETRY,  # noqa: F401
)
from ._constants import (
    PLAN_TERMINAL_IDLE as _PLAN_TERMINAL_IDLE,
)
from ._constants import (
    PLANNER_DEDUP_STATUSES as _PLANNER_DEDUP_STATUSES,  # noqa: F401
)
from ._constants import (
    PLANNER_RECENT_FAILURE_STATUS as _PLANNER_RECENT_FAILURE_STATUS,  # noqa: F401
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
from ._evolution import EvolutionMixin
from ._idle_cycle import IdleCycleMixin, _idle_exit_seconds  # noqa: F401
from ._lifecycle import LifecycleMixin
from ._mission_execution import MissionExecutionMixin
from ._planner_orchestration import PlannerOrchestrationMixin
from ._planner_rendering import PlannerRenderingMixin
from ._planning_context import PlanningContextMixin
from ._planning_cycle import PlanningCycleMixin

log = logging.getLogger(__name__)

_price_for = price_for





# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------




# Compatibility constants re-exported from ``life.supervisor``.
_PLANNER_RECENT_HISTORY_WINDOW = 20
# Plan-cycle outcome sentinels returned by ``_plan_next_work`` and consumed
# by ``run()``. Kept as a small named set (not bare string literals scattered
# across call sites) so the control flow stays auditable.
_PLAN_TASKS_ADDED = "tasks_added"
_PLAN_PROJECT_DONE = "project_done"
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
    PlanningCycleMixin,
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
        # life worker / cockpit to the same backend the engineer uses.
        self.planner_runner = planner_runner
        # Optional role-scoped skill store for the planner mission matcher.
        # Threaded from the composition root (cockpit / life worker). None keeps
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
        self._last_planner_wait_reconciliation_key: (
            tuple[str, str, str, bool] | None
        ) = None
        self._planner_waits_since_reconciliation = 0
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
            yield_provider = getattr(
                self.config,
                "manager_pipeline_yield_provider",
                None,
            )
            if callable(yield_provider):
                try:
                    manager_yield = bool(yield_provider())
                except Exception:  # noqa: BLE001 - a stale marker must not crash work
                    manager_yield = False
                if manager_yield:
                    stopped_by = "manager_config_pending"
                    break
            # Hot-reload continuous config from provider (disk, etc.)
            self._reload_continuous_config()
            stop_reason = self._maybe_stop()
            if stop_reason:
                if stop_reason == "paused_budget":
                    self._enter_pause_backoff()
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
                    pending_questions = [
                        item
                        for item in self.memory.backlog.all()
                        if str(getattr(item, "pending_question", "") or "").strip()
                    ]
                    if pending_questions:
                        sleep_s = self._enter_pause_backoff()
                        self._emit({
                            "type": "life.planner.deferred",
                            "reason": "waiting for operator answer",
                            "item_ids": [item.id for item in pending_questions],
                            "suggested_sleep_s": sleep_s,
                            "agent_layer": "planner",
                        })
                        self._emit_status(
                            "planner deferred: waiting for operator answer"
                        )
                        stopped_by = "pending_operator_question"
                        break
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
                "paused_budget",
                "paused_provider_cooldown",
                "paused_provider_fence",
                "iteration_cap",
                "lifecycle_block",
                "stage_hold",
            }:
                # No mission actually ran — this is a held/paused outcome. Escalate
                # the wait like the idle path (15→300s) instead of resetting to
                # poll_interval, so a budget pause / F5 hold doesn't busy-spin and
                # re-flood the journal every 5s until the daily cap rolls over.
                if str(outcome.get("status") or "").startswith("paused_"):
                    self._enter_pause_backoff()
                else:
                    self._enter_idle_backoff()
            else:
                # A real mission ran: clear any accumulated no-work backoff.
                self._reset_idle_backoff()
            # Auth failure flagged by _run_one: propagate immediately
            if outcome.get("auth_failure"):
                stopped_by = "auth_failure"
                break
            if outcome.get("status") == "replan_requested":
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
                planned = self._plan_next_work(revision_request=outcome)
                if planned is True:
                    continue
                if planned == "daemon_handoff":
                    stopped_by = "daemon_handoff"
                elif planned == "planner_retry":
                    stopped_by = "planner_retry"
                elif planned == _PLAN_AWAITING:
                    stopped_by = _PLAN_AWAITING
                elif planned == _PLAN_TERMINAL_IDLE:
                    stopped_by = _PLAN_TERMINAL_IDLE
                else:
                    stopped_by = "planner_error"
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
                "paused_budget",
                "paused_provider_cooldown",
                "paused_provider_fence",
                "iteration_cap",
                "lifecycle_block",
                "stage_hold",
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
                "outcome_class": mission_outcome_class(
                    status="supervisor_error",
                    success=False,
                ),
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
            return {
                "status": "paused_budget",
                "item_id": item.id,
                "reason": reason,
                "recoverable": True,
            }

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
                "status": "paused_budget",
                "item_id": item.id,
                "reason": reserve_reason,
                "recoverable": True,
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

    def _planner_verdict_metadata(self) -> dict[str, Any]:
        project = getattr(self.memory, "project", None)
        project_id = str(
            getattr(project, "fingerprint", "")
            or getattr(self.memory, "fingerprint", "")
            or Path(getattr(self.memory, "root", self._artifact_root())).name
        )
        mission_id = ""
        research_result = None
        try:
            entries = self.memory.journal.all()
        except Exception:  # noqa: BLE001
            entries = []
        for entry in reversed(entries):
            extra = getattr(entry, "extra", None)
            if not isinstance(extra, dict):
                continue
            if not mission_id:
                mission_id = str(
                    extra.get("mission_id")
                    or extra.get("attempt_id")
                    or extra.get("item_id")
                    or ""
                )
            if research_result is None:
                from ...core.research_contract import (
                    adapt_legacy_research_result_payload,
                )

                research_result = adapt_legacy_research_result_payload(extra)
            if mission_id and research_result is not None:
                break
        from ...core.research_contract import resolve_research_target_level

        return {
            "project_id": project_id,
            "mission_id": mission_id,
            "research_target_level": resolve_research_target_level(
                self._artifact_root()
            ),
            "correctness_status": (
                research_result["correctness_status"] if research_result else None
            ),
            "novelty_status": (
                research_result["novelty_status"] if research_result else None
            ),
            "significance_status": (
                research_result["significance_status"] if research_result else None
            ),
        }

    def _emit_planner_verdict(
        self,
        *,
        status: PlannerVerdictStatus,
        reason: str,
        completion_kind: str,
        resume_outcome: bool | str,
        terminal_signature: str = "",
        **details: Any,
    ) -> bool:
        event = build_planner_verdict_event(
            status=status,
            reason=reason,
            completion_kind=completion_kind,
            **self._planner_verdict_metadata(),
            **details,
        )
        event["delivery_id"] = planner_verdict_delivery_id(event)
        try:
            record = write_planner_verdict_outbox(
                self.memory.root,
                event=event,
                outcome=resume_outcome,
                terminal_signature=terminal_signature,
            )
        except OSError as exc:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": details.get("cycle", self._planning_cycles),
                "error": f"planner verdict outbox write failed: {type(exc).__name__}: {exc}",
                "reason": reason,
            })
            return False
        if not self._emit(event):
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": details.get("cycle", self._planning_cycles),
                "error": "planner verdict delivery failed; queued for idempotent retry",
                "reason": reason,
                "delivery_id": event["delivery_id"],
            })
            return False
        try:
            record = mark_planner_verdict_delivered(self.memory.root, record)
        except OSError as exc:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": details.get("cycle", self._planning_cycles),
                "error": f"planner verdict outbox acknowledgement failed: {type(exc).__name__}: {exc}",
                "reason": reason,
                "delivery_id": event["delivery_id"],
            })
            return False
        if terminal_signature:
            self._last_open_ended_project_done_signature = terminal_signature
        if status is not PlannerVerdictStatus.COMPLETED:
            clear_planner_verdict_outbox(self.memory.root)
        return bool(record.get("delivered"))

    def _retry_pending_planner_verdict(self) -> tuple[bool, bool | str | None]:
        record = load_planner_verdict_outbox(self.memory.root)
        if record is None:
            if (Path(self.memory.root) / OUTBOX_FILE).exists():
                self._emit({
                    "type": EventType.LIFE_PLANNER_ERROR,
                    "cycle": self._planning_cycles,
                    "error": "planner verdict outbox is corrupt or unreadable",
                })
                clear_planner_verdict_outbox(self.memory.root)
                return True, _PLAN_RETRY
            return False, None
        event = record["event"]
        terminal_signature = str(record.get("terminal_signature") or "")
        outcome = record.get("outcome")
        if not isinstance(outcome, (bool, str)):
            return False, None
        if (
            terminal_signature
            and self._open_ended_terminal_idle_signature() != terminal_signature
        ):
            clear_planner_verdict_outbox(self.memory.root)
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": event.get("cycle", self._planning_cycles),
                "error": "discarded stale planner verdict outbox after semantic state change",
                "reason": event.get("reason", ""),
                "delivery_id": record.get("delivery_id", ""),
            })
            return False, None
        if record.get("delivered"):
            if terminal_signature and self.config.open_ended:
                self._last_open_ended_project_done_signature = terminal_signature
                return False, None
            clear_planner_verdict_outbox(self.memory.root)
            return True, outcome

        delivery_id = str(record["delivery_id"])
        persisted = planner_verdict_was_persisted(self.memory.root, delivery_id)
        if not persisted and not self._emit(event):
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": event.get("cycle", self._planning_cycles),
                "error": "planner verdict retry failed; outbox remains pending",
                "reason": event.get("reason", ""),
                "delivery_id": delivery_id,
            })
            return True, _PLAN_RETRY
        try:
            mark_planner_verdict_delivered(self.memory.root, record)
        except OSError as exc:
            self._emit({
                "type": EventType.LIFE_PLANNER_ERROR,
                "cycle": event.get("cycle", self._planning_cycles),
                "error": f"planner verdict retry acknowledgement failed: {type(exc).__name__}: {exc}",
                "reason": event.get("reason", ""),
                "delivery_id": delivery_id,
            })
            return True, _PLAN_RETRY
        if terminal_signature:
            self._last_open_ended_project_done_signature = terminal_signature
        else:
            clear_planner_verdict_outbox(self.memory.root)
        if event.get("restart_daemon"):
            restart_reason = str(event.get("restart_reason") or event.get("reason") or "")
            if self._handle_planner_restart(restart_reason):
                return True, _PLAN_HANDOFF
            return True, _PLAN_ERROR
        return True, outcome

    def _emit(self, event: dict[str, Any]) -> bool:
        try:
            accepted = self.sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: event sink raised")
            return False
        delivered = accepted is not False
        if delivered and str(event.get("type") or "") == EventType.LIFE_BUDGET_PAUSE:
            self._publish_budget_pause_message(event)
        return delivered

    def _publish_budget_pause_message(self, event: dict[str, Any]) -> None:
        """Surface a durable, deduplicated budget pause in the Manager chat."""
        try:
            import hashlib

            from ...core.operator_messages import publish_operator_message

            project = getattr(self.memory, "project", None)
            life_dir = getattr(project, "root", None) or getattr(self.memory, "root", None)
            if life_dir is None:
                return
            item_id = str(event.get("item_id") or "")
            title = str(event.get("title") or "current task").strip()
            reason = str(event.get("reason") or "budget cap reached").strip()
            signature = hashlib.sha256(f"{item_id}\0{reason}".encode("utf-8")).hexdigest()[:16]
            text = (
                "Budget pause · 预算不足，任务已暂停。\n"
                f"Task: {title}\n"
                f"Reason: {reason}\n"
                "任务状态与 CHECKPOINT.md 已保留；提高项目预算后可以继续。"
            )
            publish_operator_message(
                life_dir,
                text=text,
                message_id=f"budget-pause-{signature}",
                event_fields={
                    "budget_pause": True,
                    "item_id": item_id,
                    "reason": reason,
                },
            )
        except Exception:  # noqa: BLE001 - alerting must not break supervision
            log.exception("life supervisor: failed to publish budget pause chat alert")

    def _emit_status(self, text: str) -> None:
        self._emit({"type": "life.status", "text": text})
