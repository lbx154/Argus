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

import hashlib
import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.ports import EventSink
from ...core.pricing import price_for, usd_for_tokens
from ..memory import BacklogItem
from ..project_lifecycle import (
    LifecycleEvent,
    ProjectState,
    apply_event,
    decide_next_state,
    infer_observable_status,
    is_token_allocatable,
)
from ..project_lifecycle_io import (
    LifecycleIOError,
    apply_persisted_to_status,
)
from ..project_lifecycle_io import (
    append_event as _lifecycle_append_event,
)
from ..project_lifecycle_io import (
    lifecycle_path as _lifecycle_path,
)
from ..project_lifecycle_io import (
    load_persisted as _lifecycle_load_persisted,
)
from ._config import (
    LifeSupervisorConfig,
    _MemoryView,
    _MissionRunner,
)
from ._cost import _CostTrackingSink
from ._helpers import (
    _entry_task_signature,
    _is_recent_no_progress_failure,
    _legacy_final_submission_marker,
    _normalize_planner_text,
    _operator_only_external_blocker_wait_reason_for_project,
    _planner_task_signature,
    _resolve_task_dep_ids,
    _sanitize_planner_task_text,
)
from ._subagent_family_failures import (
    SubagentFamilyFailure,
    recent_subagent_family_failures,
)

log = logging.getLogger(__name__)

_price_for = price_for





# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------




_PLANNER_DEDUP_STATUSES = {"pending", "running", "done"}
_PLANNER_RECENT_HISTORY_WINDOW = 20
_PLANNER_RECENT_FAILURE_STATUS = "no_progress"
_PLANNER_SCOPE_BOUNDED = "bounded"
_PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"

# Plan-cycle outcome sentinels returned by ``_plan_next_work`` and consumed
# by ``run()``. Kept as a small named set (not bare string literals scattered
# across call sites) so the control flow stays auditable.
_PLAN_TASKS_ADDED = "tasks_added"
_PLAN_PROJECT_DONE = "project_done"
_PLAN_RETRY = "planner_retry"
_PLAN_HANDOFF = "daemon_handoff"
_PLAN_ERROR = "planner_error"
_PLAN_AWAITING = "awaiting_external"
_PLAN_TERMINAL_IDLE = "planner_terminal_idle"
_PLAN_MANAGER_ROLLBACK = "manager_blocked_rollback"

# Idle backoff for the "no new work" outcomes (awaiting-external / planner
# retry / planner error). Each consecutive idle plan-cycle doubles the host's
# re-check sleep, capped — so a project correctly waiting on a live external
# job (or a planner that keeps finding nothing) is polled every few minutes,
# not continuously. Reset to 0 the moment real work runs.
_IDLE_BACKOFF_BASE_SECONDS = 15.0
_IDLE_BACKOFF_CAP_SECONDS = 300.0

# Idle auto-exit: a continuous daemon that has had no real mission to run for
# longer than this wall-clock window exits cleanly (``stopped_by=idle_timeout``)
# instead of spinning 7×24 on an empty backlog. The session model makes daemons
# cheap to respawn (a `--resume`/`--continue` brings it right back), so an idle
# daemon should release its slot rather than hold it forever. Default 30 min;
# ``ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN=0`` disables it (old never-exit behaviour).
_DAEMON_IDLE_EXIT_DEFAULT_MINUTES = 30.0


def _idle_exit_seconds() -> float:
    """Idle wall-clock (s) before a continuous daemon auto-exits; 0 = never."""
    raw = os.environ.get("ARGUS_SKILL_DAEMON_IDLE_EXIT_MIN", "").strip()
    if not raw:
        return _DAEMON_IDLE_EXIT_DEFAULT_MINUTES * 60.0
    try:
        minutes = float(raw)
    except ValueError:
        return _DAEMON_IDLE_EXIT_DEFAULT_MINUTES * 60.0
    return max(0.0, minutes) * 60.0


def _per_mission_distill_enabled() -> bool:
    """Whether to distill a reusable skill from EACH mission's process data at
    completion. OFF by default (it's an LLM classify + commit after every mission);
    ``ARGUS_SKILL_PER_MISSION_DISTILL=1`` opts in. When off, distillation runs only
    on clean daemon shutdown."""
    return os.environ.get("ARGUS_SKILL_PER_MISSION_DISTILL", "").strip().lower() in (
        "1", "true", "yes", "on")

# Re-emit an unchanged lifecycle-block status/event line at most this often
# (a heartbeat) so a long-lived blocked state stays visible without spamming
# the event timeline every tick.
_LIFECYCLE_BLOCK_HEARTBEAT_SECONDS = 1800.0

# Legacy heartbeat used by budget pauses and tests that exercise the old idle
# gate. Planner waiting/idling is now represented by structured events.
_PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS = 1800.0

# Stall escalation: after this many consecutive idle planner cycles concluding the
# same external dependency blocks progress, dispatch ONE domain-agnostic
# verification-probe mission so the agent TESTS its (possibly stale) belief against
# CURRENT reality instead of waiting forever on a memory of the blocker. Rate-limit
# repeat probes with the cooldown below.
_VERIFICATION_PROBE_AFTER_IDLE_CYCLES = 4
_VERIFICATION_PROBE_COOLDOWN_SECONDS = 1800.0

# Operator escalation: after this many consecutive missions that COMPLETED but the
# L2 reviewer judged forward_progress=false (work happened, the goal did NOT
# advance — e.g. repeated no-score / blocked-archive refuges), surface a loud,
# operator-notified stall alert. This counts ONLY the reviewer's own signal — the
# harness never decides what "progress" is; it just refuses to let the agent
# system loop invisibly without bringing the human in.
_STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS = 3
_FULL_EMNLP_GATE_DESCRIPTION = (
    "the L2 reviewer's full pipeline checklist (research → submission)"
)






# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------



# ----- thin protocol describing what we need from a MissionExecutor --------


class LifeSupervisor:
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
        self._emit({"type": "life.manager.stage_decision", **decision})
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
                    "type": "life.daemon.idle_timeout",
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
                and self._effective_full_emnlp_gate(self._artifact_root())
                and self._journal_has_full_emnlp_gate_success()
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
                        self.config.full_emnlp_gate
                        and self._journal_has_full_emnlp_gate_success()
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
        return {
            "missions_started": self._missions_started,
            "planning_cycles": self._planning_cycles,
            "results": results,
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
            self._emit({
                "type": "life.mission.completed",
                "item_id": item_id,
                "title": title,
                "objective": objective,
                "success": False,
                "status": "supervisor_error",
                "rounds": 0,
                "cost_usd": 0.0,
                "terminal_status": "supervisor_error",
                "failure_reason": failure_reason,
                "agent_layer": "supervisor",
            })
        return recovered

    def _planner_cycle_gate_reason(self) -> str:
        gate = self.config.planner_cycle_gate
        if gate is None:
            return ""
        try:
            reason = gate()
        except Exception:  # noqa: BLE001
            log.exception("planner cycle gate raised; continuing with planner")
            return ""
        return str(reason or "").strip()

    def _planner_runtime_context(self) -> str:
        provider = self.config.planner_runtime_context_provider
        if provider is None:
            return ""
        try:
            context = provider()
        except Exception:  # noqa: BLE001
            log.exception("planner runtime context provider raised; continuing")
            return ""
        return str(context or "").strip()

    def _planner_project_context(self) -> str:
        """Return cheap project-state context that keeps planner work grounded."""
        return self._planner_runtime_context()

    def _planner_runtime_with_idle_note(self) -> str:
        """Project context for the planner, prefixed — when it has been idling on
        the same blocker — with a domain-agnostic CURRENT-REALITY check so the
        planner does not stay immersed in its own stale 'awaiting ...' memory.

        Threshold 2 (below the verification-probe K) so the perception nudge
        precedes the forced probe. Domain-agnostic: it tells the planner to
        CONFIRM, never to ignore any specific blocker.
        """
        base = self._planner_project_context()
        n = int(getattr(self, "_consecutive_idle_planner_cycles", 0))
        if n < 2:
            return base
        note = (
            "CURRENT-REALITY CHECK (read before trusting the journal below): you "
            f"have idled {n} consecutive cycle(s) concluding `waiting=true` on the "
            "same blocker. Your journal may be STALE — the external dependency may "
            "already have cleared. Before concluding `waiting` again, confirm the "
            "blocker is still live against CURRENT state, not a past observation; a "
            "verification-probe mission has been or will be dispatched to test it "
            "first-hand."
        )
        return f"{note}\n\n{base}" if base else note

    def _recent_no_progress_failures(self) -> dict[tuple[str, str], Any]:
        """Return recent failed task signatures quarantined from replanning."""
        try:
            recent_entries = self.memory.journal.tail(_PLANNER_RECENT_HISTORY_WINDOW)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to read recent journal for planner")
            return {}
        matches: dict[tuple[str, str], Any] = {}
        for entry in reversed(recent_entries):
            if not _is_recent_no_progress_failure(entry):
                continue
            signature = _entry_task_signature(entry)
            if signature is None or signature in matches:
                continue
            matches[signature] = entry
        return matches

    def _recent_subagent_family_failures(self) -> dict[str, SubagentFamilyFailure]:
        """Return subagent-job families stuck in an unresolved failure streak.

        Complements ``_recent_no_progress_failures``: that mechanism only sees
        journal-level ``mission_failed``/``no_progress`` entries, which never
        fires when the SUPERVISOR's own mission is graded a success (the
        engineer really did resubmit/monitor/document real work) even though
        the subagent job it launched keeps erroring underneath. Reading the
        subagent registry directly catches that case. Fail-soft: a missing or
        unreadable registry (or a test double config without these fields)
        yields an empty dict and never blocks planning.
        """
        try:
            streak_limit = int(
                getattr(self.config, "subagent_family_failure_streak_limit", 3)
            )
        except (TypeError, ValueError):
            streak_limit = 3
        try:
            window_hours = float(
                getattr(self.config, "subagent_family_failure_window_hours", 72.0)
            )
        except (TypeError, ValueError):
            window_hours = 72.0
        if streak_limit <= 0:
            return {}
        try:
            return recent_subagent_family_failures(
                self._project_workdir(),
                window_seconds=max(0.0, window_hours) * 3600.0,
                min_streak=streak_limit,
            )
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to read subagent registry for planner")
            return {}

    @staticmethod
    def _task_mentions_family(task: Any, family: str) -> bool:
        """True if ``family`` (an experiment-family slug like
        ``swebench-verified-full-canary``) appears in the task's own text.

        Family slugs are distinctive, multi-token, hyphen/underscore-joined
        identifiers minted by the subagent tool from real run/benchmark names
        — not generic words — so a case-insensitive substring match is a safe
        heuristic here, in the same spirit as the existing (also text-based)
        duplicate-task signature match just above this loop. Checks both the
        hyphenated slug as written and its underscore variant, since planner
        prose and benchmark_family identifiers mix both conventions (e.g.
        ``swebench-verified-full-canary`` vs ``swebench_verified``).
        """
        if not family:
            return False
        haystack = " ".join((task.title, task.objective, task.evidence)).casefold()
        needle = family.casefold()
        if needle in haystack:
            return True
        return needle.replace("-", "_") in haystack.replace("-", "_")

    @staticmethod
    def _stuck_subagent_families_note(
        family_failures: dict[str, SubagentFamilyFailure],
    ) -> str:
        """Advisory fact block telling the planner which experiment families
        are currently stuck, BEFORE it proposes new tasks (not just a post-hoc
        skip). Per the harness design philosophy this only states facts and a
        constraint — it does not choose the planner's next move for it.
        """
        if not family_failures:
            return ""
        lines = [
            "STUCK EXPERIMENT FAMILIES (facts, not a directive on what to do "
            "instead): the following subagent job families have failed "
            "repeatedly, back-to-back, with no successful completion in "
            "between. A bare resubmission with an unchanged strategy will be "
            "AUTOMATICALLY SKIPPED by the supervisor (it will not reach the "
            "engineer) — propose either a materially different approach "
            "(root-cause fix, reduced scope, alternate method) or an explicit "
            "operator-escalation task instead.",
        ]
        for failure in sorted(family_failures.values(), key=lambda f: -f.streak):
            reason = f" (last failure: {failure.last_reason})" if failure.last_reason else ""
            lines.append(
                f"  - {failure.family}: {failure.streak} consecutive "
                f"{failure.last_state} attempt(s), most recently "
                f"{failure.last_task_id!r}{reason}"
            )
        return "\n".join(lines)

    def _handle_planner_restart(self, reason: str) -> bool:
        handler = self.config.planner_restart_handler
        if handler is None:
            return False
        try:
            return bool(handler(reason))
        except Exception:  # noqa: BLE001
            log.exception("planner restart handler raised; continuing")
            return False

    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        hook = self.config.post_mission_hook
        if hook is None:
            return ""
        try:
            return str(hook(outcome) or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("post mission hook raised; continuing")
            return ""

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

        ok, reason = self.config.budget.can_start(
            item=item, journal=self.memory.journal
        )
        if not ok:
            # Don't fail the item — it'll be retried next supervisor
            # run when the daily cap rolls over. Emit a heartbeat-gated event
            # so a long budget pause cannot flood the timeline.
            self._emit_status(f"budget block: {reason}")
            if self._should_journal_idle_repeat("budget_pause"):
                self._emit({
                    "type": "life.budget.pause",
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

        result = self._run_one(item)
        return result

    def _maybe_skip_inapplicable_final_submission_item(
        self,
        item: BacklogItem,
    ) -> dict[str, Any] | None:
        """Retire stale paper-final tasks when the active vertical is bounded.

        ``scope:final_submission`` only has meaning when the active vertical's
        completion gate is ``full_emnlp``. If a stale default ``research`` state
        caused the planner to enqueue a final-submission proof for a
        Manager-authored bounded domain (for example ``perf_tuning``), do not
        spend another engineer/reviewer round proving the paper pipeline is
        missing. Mark the planner artifact ``skipped`` and let the bounded
        project reach its own terminal planner verdict.
        """
        if self._planner_scope_from_item(item) != _PLANNER_SCOPE_FINAL_SUBMISSION:
            return None
        if self._effective_full_emnlp_gate(self._artifact_root()):
            return None

        reason = (
            "skipped stale final_submission task: active vertical completion "
            "gate is not full_emnlp"
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
    # F5 project-lifecycle gate
    # ------------------------------------------------------------------

    def _lifecycle_root(self) -> Path:
        """Directory holding this project's ``lifecycle.json`` sidecar.

        Prefer the per-project telemetry/life dir so lifecycle state is
        isolated per project. Fall back to the global memory root when no
        telemetry dir is configured (non-daemon ``life run`` / tests), which
        preserves the historical single-file behavior.
        """
        tdir = getattr(self.config, "telemetry_dir", None)
        if tdir is not None:
            return Path(tdir)
        return Path(getattr(self.memory, "root", None) or ".")

    def _migrate_global_lifecycle_if_needed(self, per_root: Path) -> None:
        """One-time carry-over of the legacy GLOBAL lifecycle sidecar.

        Historically lifecycle.json lived under the global memory root and was
        (incorrectly) shared across projects. When a per-project dir is now in
        use and has no sidecar yet, copy the legacy global file in once, then
        retire the global file (rename to ``*.migrated``) so future projects
        start clean instead of inheriting a mis-keyed shared state. Best-effort
        and idempotent; only runs in the per-project (telemetry_dir) regime.
        """
        if getattr(self.config, "telemetry_dir", None) is None:
            return
        if getattr(self, "_lifecycle_migrated", False):
            return
        self._lifecycle_migrated = True
        try:
            per_file = _lifecycle_path(per_root)
            if per_file.exists():
                return
            global_root = Path(getattr(self.memory, "root", None) or ".")
            if global_root == per_root:
                return
            global_file = _lifecycle_path(global_root)
            if not global_file.exists():
                return
            per_root.mkdir(parents=True, exist_ok=True)
            data = global_file.read_text(encoding="utf-8")
            tmp = per_file.with_name(per_file.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, per_file)
            try:
                global_file.replace(
                    global_file.with_name(global_file.name + ".migrated")
                )
            except OSError:
                log.warning(
                    "lifecycle: copied global sidecar to %s but could not "
                    "retire %s; future projects may inherit it",
                    per_file, global_file,
                )
            log.info(
                "lifecycle: migrated legacy global sidecar into per-project "
                "dir %s", per_file,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "lifecycle migration failed; continuing with fresh "
                "per-project state"
            )

    def _maybe_block_on_lifecycle(
        self, item: BacklogItem
    ) -> dict[str, Any] | None:
        """Run one F5 tick and decide whether to short-circuit dispatch.

        Returns ``None`` when the project is allocatable (supervisor
        proceeds to ``_run_one``). Returns a status dict when blocked —
        the daemon treats this like a budget pause.

        Fail-soft: any exception in the lifecycle path is logged and
        treated as "allocatable" so a corrupt sidecar can never wedge
        the supervisor.
        """
        try:
            memory_root = self._lifecycle_root()
            self._migrate_global_lifecycle_if_needed(memory_root)
            project_root = self._project_workdir()
            spent_usd, budget_usd = self._lifecycle_budget_snapshot()

            status = infer_observable_status(
                project_root,
                project_id=memory_root.name,
                budget_usd=budget_usd,
                spent_usd=spent_usd,
            )
            try:
                persisted = _lifecycle_load_persisted(memory_root)
            except LifecycleIOError as exc:
                log.warning(
                    "lifecycle sidecar at %s is malformed (%s); "
                    "treating project as fresh",
                    memory_root, exc,
                )
                persisted = {}
            status = apply_persisted_to_status(status, persisted)

            # EMNLP completion authority is the L2 reviewer's
            # ``final_submission`` certification — NOT the mere presence
            # of ``paper/main.pdf`` (which the agent compiles for format
            # preflight long before the draft is submission-ready). The
            # generic F5 rule ``submission_artifact_present -> DONE`` is
            # therefore premature for an uncertified full-EMNLP mission,
            # and DONE is terminal + non-allocatable, so it permanently
            # starves the project of tokens. Defer to the reviewer:
            #
            #   (a) repair an already-persisted bad DONE back to WRITING
            #       once (preserving history via append_event), and
            #   (b) suppress a fresh ``submission_artifact_present`` DONE
            #       transition *before* it is applied/persisted
            #       so we don't re-fire + spam the event timeline every tick.
            #
            # When the reviewer truly certifies, supervisor.run() auto-
            # stops via ``_journal_has_full_emnlp_gate_success`` instead.
            artifact_root = (
                self._artifact_root() if hasattr(self, "_artifact_root") else memory_root
            )
            uncertified_full_emnlp = (
                self._effective_full_emnlp_gate(artifact_root)
                and not self._journal_has_full_emnlp_gate_success()
            )
            if (
                uncertified_full_emnlp
                and status.state == ProjectState.DONE
                and persisted.get("state") == ProjectState.DONE.value
            ):
                from datetime import datetime, timezone

                repair_event = LifecycleEvent(
                    at=datetime.now(timezone.utc),
                    from_state=ProjectState.DONE,
                    to_state=ProjectState.WRITING,
                    reason="full_emnlp_gate_not_certified",
                )
                status = apply_event(status, repair_event)
                try:
                    _lifecycle_append_event(
                        memory_root,
                        new_status=status,
                        event=repair_event,
                    )
                except OSError as exc:
                    log.warning(
                        "could not repair premature-DONE lifecycle "
                        "sidecar at %s: %s",
                        memory_root, exc,
                    )
                self._emit({
                    "type": "life.lifecycle.transition",
                    "from_state": ProjectState.DONE.value,
                    "to_state": ProjectState.WRITING.value,
                    "reason": "full_emnlp_gate_not_certified",
                    "agent_layer": "supervisor",
                })

            event = decide_next_state(status)
            if (
                uncertified_full_emnlp
                and event is not None
                and event.to_state == ProjectState.DONE
                and event.reason == "submission_artifact_present"
            ):
                # Suppress non-persistently: drop the event entirely so it
                # is never applied or persisted. The project
                # stays WRITING (allocatable) until the reviewer certifies.
                event = None
            if event is not None:
                status = apply_event(status, event)
                try:
                    _lifecycle_append_event(
                        memory_root,
                        new_status=status,
                        event=event,
                    )
                except OSError as exc:
                    log.warning(
                        "could not persist lifecycle transition to %s: %s",
                        memory_root, exc,
                    )
                self._emit({
                    "type": "life.lifecycle.transition",
                    "from_state": event.from_state.value,
                    "to_state": event.to_state.value,
                    "reason": event.reason,
                    "agent_layer": "supervisor",
                })

            # Advisory time signals (incubating_time / running_evidence_gap
            # / writing_idle) are PULLED on demand by --status / cockpit /
            # telegram digest, not PUSHED into the event timeline every tick.
            # See ``advisory_time_signals`` in project_lifecycle.py. The
            # harness must not spam the timeline with non-event facts.

            if not is_token_allocatable(status):
                # Log hygiene: the held-item status/event line is identical
                # every tick a project sits in the same non-allocatable state.
                # Emit only when the (state, item) signature changes
                # or a heartbeat interval elapses — otherwise a long block used
                # to flood events.jsonl with tens of thousands
                # of identical lines. Dispatch behavior is unchanged: we always
                # return the block dict.
                state_value = status.state.value
                sig = (state_value, item.id)
                now = time.monotonic()
                reason = (
                    f"project lifecycle is {state_value}; "
                    f"resume with --lifecycle-resume or archive with "
                    f"--lifecycle-archive"
                )
                last_sig = getattr(self, "_last_lifecycle_block_sig", None)
                last_at = getattr(self, "_last_lifecycle_block_at", 0.0)
                should_emit = (
                    sig != last_sig
                    or (now - last_at) >= _LIFECYCLE_BLOCK_HEARTBEAT_SECONDS
                )
                if should_emit:
                    self._last_lifecycle_block_sig = sig
                    self._last_lifecycle_block_at = now
                    self._emit_status(
                        f"lifecycle gate: project state={state_value}; "
                        f"backlog item {item.id!r} held"
                    )
                    self._emit({
                        "type": "life.lifecycle.block",
                        "item_id": item.id,
                        "title": item.title,
                        "lifecycle_state": state_value,
                        "reason": reason,
                        "agent_layer": "supervisor",
                    })
                return {
                    "status": "lifecycle_block",
                    "item_id": item.id,
                    "lifecycle_state": state_value,
                    "reason": reason,
                }
        except Exception:  # noqa: BLE001
            log.exception("lifecycle gate failed; allowing dispatch")
        return None

    def _lifecycle_budget_snapshot(self) -> tuple[float, float]:
        """Best-effort (spent_usd, total_budget_usd) for F5 budget gate.

        LifeBudget tracks per-mission and daily caps; we approximate the
        project's total budget as ``daily_cap * 30`` (a month of running)
        and spent as the event-recorded cumulative cost. If either is
        unavailable, returns ``(0.0, 0.0)`` and the F5 budget gate stays
        dormant for that tick.
        """
        try:
            budget = getattr(self.config, "budget", None)
            if budget is None:
                return (0.0, 0.0)
            daily = float(getattr(budget, "daily_cap_usd", 0.0) or 0.0)
            total = daily * 30.0
            spent = 0.0
            try:
                spent = float(
                    sum(
                        float(getattr(e, "cost_usd", 0.0) or 0.0)
                        for e in self.memory.journal.all()
                    )
                )
            except Exception:  # noqa: BLE001
                spent = 0.0
            return (spent, total)
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

    # ------------------------------------------------------------------
    # One mission
    # ------------------------------------------------------------------

    def _effective_per_mission_cap(self, item: BacklogItem) -> float:
        """The cap enforced for ``item`` (min of operator per-item budget and the
        global per-mission cap). Delegates to ``LifeBudget`` so the preflight
        ``can_start`` check and the mid-mission breaker use one number (F3)."""
        return self.config.budget.effective_per_mission_cap(item)

    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        prelude = self.memory.render_prelude()
        item_metadata = self._render_backlog_item_metadata(item)
        if item_metadata:
            prelude = item_metadata + "\n---\n\n" + prelude if prelude else item_metadata
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
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
            # Carry the objective on the event itself (not just the journal
            # entry) so the live follow / REPL mission-context line renders the
            # real goal instead of "objective=-".
            "objective": item.objective,
            "missions_started": self._missions_started,
        })
        # Phase-change callback.
        def _phase_cb(layer: str, info: dict[str, Any]) -> None:
            try:
                self._emit({
                    "type": "life.phase.started",
                    "item_id": item.id,
                    "agent_layer": layer,
                    "round_index": info.get("round_index", 0),
                })
            except Exception:  # noqa: BLE001
                log.debug("phase_change event failed; non-critical")

        cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
        )

        telemetry_monitor: Any = None
        if self.config.telemetry_dir is not None:
            try:
                from ..telemetry import MissionTelemetryMonitor
                telemetry_monitor = MissionTelemetryMonitor(
                    life_dir=self.config.telemetry_dir,
                    workdir=self._project_workdir(),
                    item_id=item.id,
                    title=item.title,
                    interval_seconds=self.config.telemetry_interval_seconds,
                    stop_event=self.config.stop_event,
                )
                telemetry_monitor.start()
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to start telemetry monitor")

        outcome: Any = None
        exc_str: str | None = None
        t0 = time.time()
        # Per-item codex SESSION ISOLATION (anti context-pollution). The runner
        # chains its codex thread across execute() calls; left unchecked, a brand
        # new, unrelated backlog item RESUMES the previous mission's session and
        # inherits all its context (a plain "你上一个任务干了什么" was resuming a
        # kernel-optimization session and reading its GROUND_TRUTH). A NEW item
        # must start a FRESH session; only iteration cycles of the SAME item keep
        # the thread for continuity. Curated cross-mission memory still flows via
        # the checkpoint/prelude — this only resets the raw thread bleed.
        if getattr(self, "_last_mission_item_id", None) != item.id:
            for _attr in ("_next_seed_thread_id", "last_thread_id"):
                try:
                    if hasattr(self.runner, _attr):
                        setattr(self.runner, _attr, None)
                except Exception:  # noqa: BLE001
                    pass
        self._last_mission_item_id = item.id
        try:
            execute_kwargs: dict[str, Any] = {
                "objective": item.objective,
                "sink": cost_sink,
                "prelude_context": prelude,
                "scope": self._planner_scope_from_item(item),
            }
            original_objective = (
                getattr(item, "original_objective", "") or item.objective
            )
            # F3: a LIVE per-mission budget probe — the engine reads cost_sink each
            # round and hard-stops if the effective cap is reached mid-mission.
            from ._config import MissionBudget
            mission_budget = MissionBudget(
                cap_usd=self._effective_per_mission_cap(item),
                spent=cost_sink.total_usd,
            )
            try:
                from inspect import Parameter, signature

                params = signature(self.runner.execute).parameters
                _accepts_kw = any(
                    p.kind == Parameter.VAR_KEYWORD for p in params.values()
                )
                if "original_objective" in params or _accepts_kw:
                    execute_kwargs["original_objective"] = original_objective
                if "per_mission_budget" in params or _accepts_kw:
                    execute_kwargs["per_mission_budget"] = mission_budget
                if "preplanned" in params or _accepts_kw:
                    execute_kwargs["preplanned"] = any(
                        str(tag).strip().lower() == "planner"
                        for tag in getattr(item, "tags", [])
                    )
            except (TypeError, ValueError):
                execute_kwargs["original_objective"] = original_objective
                execute_kwargs["per_mission_budget"] = mission_budget
            outcome = self.runner.execute(**execute_kwargs)
        except Exception as exc:  # noqa: BLE001
            exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
        finally:
            if telemetry_monitor is not None:
                try:
                    telemetry_monitor.stop()
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: failed to stop telemetry monitor")
        elapsed = time.time() - t0

        success = bool(getattr(outcome, "success", False)) if outcome else False
        status = str(getattr(outcome, "status", "error") if outcome else "error")
        rounds = int(getattr(outcome, "rounds", 0) or 0)
        stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        usd = cost_sink.total_usd()

        # Auth failure: the codex backend detected an expired/invalid
        # token. Stop this drain pass so we do not immediately continue
        # with stale credentials, but do not signal the daemon's global
        # stop_event. A 7x24 worker should stay alive so it can recover
        # after credentials are refreshed, and transient provider errors
        # should not kill the supervising process.
        auth_failure = bool(getattr(outcome, "auth_failure", False))
        if auth_failure:
            self._emit({
                "type": "life.auth_failure",
                "item_id": item.id,
                "text": (
                    "⚠️  codex authentication failed — run `codex login` "
                    "to refresh credentials if this persists; the daemon "
                    "will keep polling."
                ),
            })

        # The post-mission critic/polish iteration loop was removed (the L1
        # engineer works, the L2 reviewer verifies — no separate critic agent).
        # The ``iteration`` journal/event keys below are kept EMPTY only for
        # schema back-compat. / 事后 critic/迭代循环已移除；下方 journal/event 的
        # ``iteration`` 字段保留为空，仅为 schema 向后兼容。

        # F3: the mid-mission cost breaker fired — PAUSE, do not fail/complete.
        # Roll the item back to PENDING (next tick re-runs from its checkpoint).
        # Anti-cheat: a budget-stopped mission is NEVER marked done/success —
        # the reviewer stays the sole done-ness authority.
        if status == "budget_exhausted":
            cap = self._effective_per_mission_cap(item)
            self.memory.backlog.update(item.id, status="pending")
            self._emit({
                "type": "life.mission.completed",
                "item_id": item.id,
                "success": False,
                "status": "budget_pause",
                "cost_usd": usd,
                "cap_usd": cap,
                "spent_usd": usd,
            })
            return {"status": "budget_pause", "item_id": item.id, "cost_usd": usd}

        # Update backlog row.
        if success:
            self.memory.backlog.mark_done(item.id)
        else:
            err = exc_str or stop_reason or "unspecified failure"
            self.memory.backlog.mark_failed(item.id, error=err)

        # A "blocked" verdict means the REVIEWER stopped progress because it
        # needs the OPERATOR to make a call — not a bug/crash. Persist the
        # question onto the (now-terminal) item so it outlives this one event:
        # /status can list every currently-unanswered question across ALL
        # projects/restarts, not just whatever the REPL happened to be tailing
        # live when it was asked (see manager/repl.py's old chat_state-only
        # ``blocked_question``, which was lost the moment that process exited).
        # Writing a non-status field onto an already-terminal item is legal —
        # Backlog.update()'s IllegalStateTransition seal only guards STATUS
        # transitions, not other fields.
        if status == "blocked":
            operator_question = str(
                getattr(outcome, "operator_question", "") or ""
            ).strip()
            if operator_question:
                try:
                    self.memory.backlog.update(
                        item.id, pending_question=operator_question,
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "life supervisor: failed to persist pending_question"
                    )

        kind = "mission_complete" if success else "mission_failed"
        final_submission_certified = bool(
            kind == "mission_complete"
            and self._planner_scope_from_item(item) == _PLANNER_SCOPE_FINAL_SUBMISSION
            and getattr(outcome, "final_submission_certified", False)
        )
        planner_report = (
            getattr(outcome, "planner_report", {})
            if isinstance(getattr(outcome, "planner_report", {}), dict)
            else {}
        )
        checklist_feedback = (
            getattr(outcome, "checklist_feedback", {})
            if isinstance(getattr(outcome, "checklist_feedback", {}), dict)
            else {}
        )
        step_back = (
            getattr(outcome, "step_back", None)
            if isinstance(getattr(outcome, "step_back", None), dict)
            else None
        )
        completion_summary = self._completion_evidence_from_outcome(outcome)
        if final_submission_certified:
            self._persist_final_submission_certification(title=item.title)

        self._update_no_progress_streak(
            kind=kind, report=getattr(outcome, "planner_report", {})
        )

        self._emit({
            "type": "life.mission.completed",
            "item_id": item.id,
            "title": item.title,
            "objective": item.objective,
            "success": success,
            "status": status,
            "rounds": rounds,
            "elapsed_seconds": elapsed,
            "cost_usd": usd,
            "planner_task_signature": {
                "title": _normalize_planner_text(item.title),
                "objective": _normalize_planner_text(item.objective),
            }
            if kind == "mission_failed"
            else {},
            "terminal_status": status if kind == "mission_failed" else "",
            "stop_reason": (stop_reason or err) if kind == "mission_failed" else "",
            "failure_reason": err if kind == "mission_failed" else "",
            "agent_layer": "engineer",
            "engineer_model": self.engineer_model,
            "reviewer_model": self.reviewer_model,
            "scientist_cost_usd": cost_sink.scientist_usd(),
            "engineer_cost_usd": cost_sink.engineer_usd(),
            "reviewer_cost_usd": cost_sink.reviewer_usd(),
            # util (manager/classify) + copilot premium-request cost were folded
            # into total_usd() but never surfaced in the breakdown — emit them so
            # the cost is fully auditable. copilot_premium_requests is the raw
            # count (GitHub bills per premium request, flat $/req — NOT per token,
            # so a copilot mission's whole dollar cost is this count * rate).
            "util_cost_usd": cost_sink.util_usd(),
            "copilot_cost_usd": cost_sink.copilot_usd(),
            "copilot_premium_requests": cost_sink.copilot_premium_requests,
            "scientist_input_tokens": cost_sink.scientist_input_tokens,
            "scientist_cached_input_tokens": (
                cost_sink.scientist_cached_input_tokens
            ),
            "scientist_output_tokens": cost_sink.scientist_output_tokens,
            "scientist_reasoning_output_tokens": (
                cost_sink.scientist_reasoning_output_tokens
            ),
            "scientist_usage_by_model": {
                model: {
                    "input_tokens": values[0],
                    "cached_input_tokens": values[1],
                    "output_tokens": values[2],
                    "reasoning_output_tokens": values[3],
                }
                for model, values in cost_sink.scientist_usage_by_model.items()
            },
            "input_tokens": cost_sink.total_input_tokens(),
            "output_tokens": cost_sink.total_output_tokens(),
            "reasoning_output_tokens": cost_sink.total_reasoning_output_tokens(),
            "matched_skill": str(getattr(outcome, "matched_skill_name", "") or ""),
            "skill_distilled": bool(getattr(outcome, "skill_distilled", False)),
            "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
            "completion_summary": completion_summary,
            "planner_report": planner_report,
            "checklist_feedback": checklist_feedback,
            "step_back": step_back,
            "final_submission_certified": final_submission_certified,
            "iteration": None,
        })

        # Manager "janitor": when a mission completes successfully, review the
        # Manager "janitor": when a mission completes successfully, review the
        # runtime library's distilled skills and write each back into the argus
        # SOURCE tree — a cross-domain capability → builtin_skills/, a
        # domain-specific one → verticals/<v>/skills/ — then commit. Anything too
        # specific is left in the runtime library. Fully fail-soft; never blocks
        # completion.
        if kind == "mission_complete" and _per_mission_distill_enabled():
            try:
                # Per-mission skill distillation: classify THIS mission's process
                # data into a reusable skill (builtin / vertical) + commit, so the
                # next mission inherits what was learned. OFF by default and gated
                # on ARGUS_SKILL_PER_MISSION_DISTILL because it is an LLM classify +
                # source write after EVERY mission across all live daemons (a real
                # cost). When the gate is off, distillation happens only on clean
                # daemon shutdown (see life_worker._distill_on_shutdown).
                from ...manager.skill_tidy import tidy_after_mission

                counts = tidy_after_mission(
                    self._project_workdir(),
                    self.runner,
                    on_event=self._emit,
                )
                if counts.get("to_builtin") or counts.get("to_vertical"):
                    log.info("manager skill tidy-up after mission: %s", counts)
            except Exception:  # noqa: BLE001 — tidy must never break completion
                log.warning("manager skill tidy-up after mission failed", exc_info=True)

        return {
            "item_id": item.id,
            "title": item.title,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "iteration": None,
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

    def _idle_backoff_seconds(self) -> float:
        """Exponential re-check sleep for consecutive no-work plan-cycles.

        ``_consecutive_idle_planner_cycles`` is incremented by the caller
        BEFORE calling this; cycle 1 → base, doubling each cycle, capped.
        """
        n = max(1, int(self._consecutive_idle_planner_cycles))
        return min(_IDLE_BACKOFF_CAP_SECONDS, _IDLE_BACKOFF_BASE_SECONDS * (2 ** (n - 1)))

    def _reset_idle_backoff(self) -> None:
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        self._idle_since = None
        self._last_open_ended_project_done_signature = ""

    def _enter_idle_backoff(self) -> float:
        """Register one more no-work plan-cycle and return the suggested sleep."""
        self._consecutive_idle_planner_cycles += 1
        if getattr(self, "_idle_since", None) is None:
            self._idle_since = time.monotonic()
        self._suggested_sleep_s = self._idle_backoff_seconds()
        return self._suggested_sleep_s

    def _maybe_idle_timeout(self) -> str:
        """``"idle_timeout"`` once a continuous daemon has been idle too long.

        Idle wall-clock is measured from ``_idle_since`` (first no-work pass)
        and spans the daemon's outer-loop sleeps. Returns ``""`` when not in
        continuous mode, when the feature is disabled (cap ≤ 0), or when the
        streak is still within the window — so the only behaviour change is: a
        genuinely idle 7×24 daemon releases its slot after the cap.
        """
        if not getattr(self.config, "continuous", False):
            return ""
        cap = _idle_exit_seconds()
        idle_since = getattr(self, "_idle_since", None)
        if cap <= 0 or idle_since is None:
            return ""
        if time.monotonic() - idle_since >= cap:
            return "idle_timeout"
        return ""

    def _should_journal_idle_repeat(self, kind: str) -> bool:
        """Heartbeat-gate repetitive idle/waiting JOURNAL appends.

        Keyed on ``kind`` ALONE — deliberately ignoring the reason text, because
        the planner rewrites the reason every cycle (fresh audit timestamps and
        details), so a reason-keyed gate would never collapse the spam. Returns
        True (and updates the suppression state) when the kind differs from the
        last idle entry or a heartbeat window has elapsed; False for an
        in-window repeat that should be suppressed — so a long external wait
        cannot flood, and poison, the planner's own next-cycle context. The
        per-cycle event + status still carry the live reason, so operator
        visibility is unchanged. State read via ``getattr`` defaults for
        test-stub safety.
        """
        now = time.monotonic()
        last_sig = getattr(self, "_last_planner_idle_sig", None)
        last_at = getattr(self, "_last_planner_idle_at", 0.0)
        if kind != last_sig or (
            now - last_at
        ) >= _PLANNER_IDLE_JOURNAL_HEARTBEAT_SECONDS:
            self._last_planner_idle_sig = kind
            self._last_planner_idle_at = now
            return True
        return False

    def _open_ended_terminal_idle_signature(self) -> str:
        """Cheap observable-state fingerprint for open-ended terminal idling.

        The signature deliberately excludes the life journal/backlog files
        written by the supervisor itself, so a skipped planner cycle does not
        invalidate its own idle state. It includes runtime context, objective,
        pipeline stage, backlog statuses, and project file metadata so operator
        edits or daemon/runtime source changes cause the planner to run again.
        """
        digest = hashlib.sha256()
        digest.update(b"open-ended-terminal-idle-v1\0")
        digest.update(str(self.config.continuous_objective or "").encode())
        digest.update(b"\0")
        digest.update(str(self._current_pipeline_stage() or "").encode())
        digest.update(b"\0")
        digest.update(str(self._planner_project_context() or "").encode())
        digest.update(b"\0")
        try:
            for item in self.memory.backlog.all():
                digest.update(str(getattr(item, "id", "")).encode())
                digest.update(b"\t")
                digest.update(str(getattr(item, "title", "")).encode())
                digest.update(b"\t")
                digest.update(str(getattr(item, "status", "")).encode())
                digest.update(b"\n")
        except Exception as exc:  # noqa: BLE001
            digest.update(f"backlog-error:{type(exc).__name__}:{exc}".encode())
            digest.update(b"\0")

        root = self._planner_workdir()
        ignored_dirs = {
            ".git",
            ".venv",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "node_modules",
        }
        ignored_files = {
            "events.jsonl",
            "journal.jsonl",
            "backlog.jsonl",
            "continuous.json",
            "daemon.log",
            "daemon.status.json",
            "daemon.pid",
        }
        try:
            root = root.resolve()
            count = 0
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d
                    for d in sorted(dirnames)
                    if d not in ignored_dirs and not d.endswith(".egg-info")
                ]
                rel_dir = Path(dirpath).relative_to(root)
                if any(part in ignored_dirs for part in rel_dir.parts):
                    continue
                for name in sorted(filenames):
                    if name in ignored_files:
                        continue
                    path = Path(dirpath) / name
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    try:
                        rel = path.relative_to(root)
                    except ValueError:
                        rel = path
                    digest.update(str(rel).encode("utf-8", "surrogateescape"))
                    digest.update(b"\t")
                    digest.update(str(st.st_size).encode())
                    digest.update(b"\t")
                    digest.update(str(st.st_mtime_ns).encode())
                    digest.update(b"\n")
                    count += 1
                    if count >= 5000:
                        digest.update(b"file-scan-truncated\0")
                        raise StopIteration
        except StopIteration:
            pass
        except Exception as exc:  # noqa: BLE001
            digest.update(f"fs-error:{type(exc).__name__}:{exc}".encode())
            digest.update(b"\0")
        return digest.hexdigest()

    def _maybe_idle_after_unchanged_open_ended_done(self) -> str | None:
        if not (
            getattr(self.config, "continuous", False)
            and getattr(self.config, "continuous_objective", "")
            and getattr(self.config, "open_ended", False)
            and getattr(self, "_last_open_ended_project_done_signature", "")
        ):
            return None

        # New operator input is state change. Drain it into the inbox context so
        # the next planner call can see it, then re-plan normally.
        if self._drain_user_inbox():
            self._last_open_ended_project_done_signature = ""
            return None

        current = self._open_ended_terminal_idle_signature()
        if current != self._last_open_ended_project_done_signature:
            self._last_open_ended_project_done_signature = ""
            return None

        sleep_s = self._enter_idle_backoff()
        self._emit({
            "type": "life.planner.terminal_idle",
            "cycle": self._planning_cycles,
            "reason": "open-ended project_done unchanged since last planner verdict",
            "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
            "suggested_sleep_s": sleep_s,
        })
        self._emit_status(
            "planner: project already done and unchanged; idling without planner call"
        )
        return _PLAN_TERMINAL_IDLE

    def _planner_task_tags(self, task: Any) -> list[str]:
        scope = self._normalize_planner_scope(getattr(task, "scope", ""))
        return ["planner", f"scope:{scope}"]

    @staticmethod
    def _normalize_planner_scope(scope: object) -> str:
        normalized = str(scope or _PLANNER_SCOPE_BOUNDED).strip().lower().replace("-", "_")
        if normalized == _PLANNER_SCOPE_FINAL_SUBMISSION:
            return _PLANNER_SCOPE_FINAL_SUBMISSION
        return _PLANNER_SCOPE_BOUNDED

    @staticmethod
    def _planner_scope_from_item(item: BacklogItem) -> str:
        for tag in item.tags:
            normalized = str(tag).strip().lower().replace("-", "_")
            if normalized in {
                f"scope:{_PLANNER_SCOPE_FINAL_SUBMISSION}",
                f"planner_scope:{_PLANNER_SCOPE_FINAL_SUBMISSION}",
            }:
                return _PLANNER_SCOPE_FINAL_SUBMISSION
            if normalized in {
                f"scope:{_PLANNER_SCOPE_BOUNDED}",
                f"planner_scope:{_PLANNER_SCOPE_BOUNDED}",
            }:
                return _PLANNER_SCOPE_BOUNDED
        return ""

    @classmethod
    def _item_is_final_submission(cls, item: BacklogItem) -> bool:
        """True when a backlog item is a project-final ``final_submission``
        task. Prefers the structured ``scope:final_submission`` tag; falls back
        to the legacy objective-prose marker only for items persisted before
        scope tagging existed (resumed-daemon compatibility)."""
        if cls._planner_scope_from_item(item) == _PLANNER_SCOPE_FINAL_SUBMISSION:
            return True
        return _legacy_final_submission_marker(getattr(item, "objective", "") or "")

    def _render_backlog_item_metadata(self, item: BacklogItem) -> str:
        scope = self._planner_scope_from_item(item)
        if not scope and not item.tags:
            return ""
        is_paper_long_horizon = self.config.paper_mission
        lines = ["## Backlog item metadata"]
        if scope:
            lines.append(f"- planner_scope: {scope}")
        if item.tags:
            lines.append("- tags: " + ", ".join(item.tags))
        if scope == _PLANNER_SCOPE_FINAL_SUBMISSION:
            lines.append(
                f"- final_submission_gate: {_FULL_EMNLP_GATE_DESCRIPTION} must be "
                "fully satisfied (every checklist item certified by the reviewer "
                "with concrete evidence) before this item can be marked done."
            )
        elif scope == _PLANNER_SCOPE_BOUNDED:
            if is_paper_long_horizon:
                lines.append(
                    "- paper_optimization_task: this is a bounded mission, but it is "
                    "part of a long-horizon paper/submission objective. First satisfy "
                    "the named acceptance criteria, then continue through adjacent "
                    "paper blockers while budget allows; do not mark done only because "
                    "one narrow check passed if the relevant stage checklist items "
                    "(manuscript, evidence, review, layout, figure/table, citation, "
                    "manifest, or assurance) are still unmet. Full-pipeline "
                    "certification is required only for `final_submission`, but fresh "
                    "concrete evidence for the items you touched is required here."
                )
            else:
                lines.append(
                    "- bounded_task: judge this item against its own acceptance criteria; "
                    "do not require the project-final EMNLP gate unless the objective "
                    "explicitly asks for it."
                )
        return "\n".join(lines)

    def _objective_with_item_scope_context(
        self,
        item: BacklogItem,
        objective: str,
    ) -> str:
        metadata = self._render_backlog_item_metadata(item)
        if not metadata:
            return objective
        return f"{metadata}\n\nOriginal operator objective:\n{objective.strip()}"

    @staticmethod
    def _completion_evidence_from_outcome(outcome: Any) -> str:
        for attr in ("final_message", "completion_summary_markdown", "stop_reason"):
            value = getattr(outcome, attr, "") or ""
            if value:
                return str(value)[:4000]
        return ""

    def _journal_has_full_emnlp_gate_success(self) -> bool:
        """Decide whether the project-final completion gate has passed.

        Source of truth (post-validator-retirement): the event timeline. A
        ``final_submission`` mission is certified complete only when the
        reviewer returns a full-pipeline completion verdict, which the
        supervisor records as a ``life.mission.completed`` event carrying
        ``final_submission_certified = True``. We no longer call the
        hardcoded ``validate_full_emnlp_readiness`` validator — the reviewer's
        checklist verdict is the single source of truth.

        Fail-closed: only an explicit certified entry counts. We scan the
        recent event-backed history tail for such an entry.
        """
        if self._final_submission_cert_path().exists():
            return True
        try:
            entries = self.memory.journal.tail(50)
        except Exception:  # noqa: BLE001
            return False
        for entry in entries:
            if getattr(entry, "kind", "") != "mission_complete":
                continue
            extra = getattr(entry, "extra", {}) or {}
            if isinstance(extra, dict) and bool(
                extra.get("final_submission_certified")
            ):
                return True
        return False

    def _effective_full_emnlp_gate(self, workdir: object) -> bool:
        """Whether the full-pipeline final-submission gate applies here.

        Returns ``self.config.full_emnlp_gate`` AND the active vertical's
        completion gate being the paper gate (``"full_emnlp"``). The
        final-submission completion gate only makes sense for a *research*
        vertical: a ``speedrun`` mission runs just the optimize+measure stages
        and has no submission package to certify, so requiring the gate would
        wedge it forever. AND-ing with the vertical's own completion gate keeps
        research behavior identical (gate stays on) while letting speedrun
        missions accept ``project_done`` straight from the run loop (gate off).
        The read side is deterministic and exception-free, so this never spends
        a token.
        """
        if not self.config.full_emnlp_gate:
            return False
        from ...skills.vertical_select import (
            VerticalResolutionError,
            resolve_vertical,
        )
        from ...verticals._base import (
            load_vertical,
            vertical_completion_gate,
        )

        try:
            vertical = resolve_vertical(workdir)
        except VerticalResolutionError:
            # The Manager has not decided + persisted the vertical yet. An
            # undecided mission is definitionally not at its final-submission
            # gate, so the gate does not apply (keep running); it is NOT a silent
            # default to research — resolve_vertical still raised loudly, we just
            # treat "no vertical yet" as "gate not satisfied" for THIS check.
            return False
        mod = load_vertical(vertical, project_root=workdir)
        return vertical_completion_gate(mod) == "full_emnlp"

    def _final_submission_cert_path(self) -> Path:
        root = Path(
            getattr(self.config, "telemetry_dir", None)
            or getattr(self.memory, "root", None)
            or "."
        )
        return root / "final_submission_certified.json"

    def _persist_final_submission_certification(self, *, title: str) -> None:
        path = self._final_submission_cert_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        payload = {
            "certified_at": time.time(),
            "title": title,
        }
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _operator_only_external_blocker_wait_reason(self) -> str:
        """Return a waiting reason for an operator-only external blocker.

        Generic: scans for operator-only external blocker artifacts,
        validates that local engineering is exhausted, and returns a human
        reason string. Empty string when nothing matches or when local action
        is still required.
        """
        return _operator_only_external_blocker_wait_reason_for_project(
            self._project_workdir()
        )

    @staticmethod
    def _operator_external_blocker_short_circuit_decision(
        *, project_root: Path
    ) -> Any | None:
        """Return a waiting verdict before planner runs when operator-only
        external artifacts are still absent.
        """
        reason = _operator_only_external_blocker_wait_reason_for_project(project_root)
        if not reason:
            return None
        from ...planner.planner import PlannerVerdict

        return PlannerVerdict(
            project_done=False,
            reason=(
                f"{reason}; skipping planner cycle to avoid impossible "
                "repair-task loop"
            ),
            waiting=True,
            waiting_reason=(
                f"{reason}; skipping planner cycle to avoid impossible "
                "repair-task loop"
            ),
            new_tasks=[],
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
        )

    def _defer_project_done_for_operator_external_blocker(self, verdict: Any) -> Any:
        if not (
            getattr(verdict, "project_done", False)
            and self._effective_full_emnlp_gate(self._artifact_root())
            and not self._journal_has_full_emnlp_gate_success()
        ):
            return verdict
        wait_reason = self._operator_only_external_blocker_wait_reason()
        if not wait_reason:
            return verdict
        return replace(
            verdict,
            project_done=False,
            waiting=True,
            waiting_reason=wait_reason,
            reason=wait_reason,
            new_tasks=[],
        )

    def _manager_intent_context(self) -> dict[str, Any]:
        """Latest user-intent interpretation from the canonical events timeline."""
        try:
            project = getattr(self.memory, "project", None)
            root = getattr(project, "root", None)
            if root is None:
                root = getattr(self.config, "telemetry_dir", None)
            if root is None:
                root = getattr(self.memory, "root", None)
            if root is None:
                return {}
            data: dict[str, Any] | None = None
            for name in ("events.jsonl", "events.jsonl.1"):
                path = Path(root) / name
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                for raw in reversed(lines):
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if str(event.get("type") or "").startswith("life.manager.intent."):
                        data = event
                        break
                if data is not None:
                    break
            if data is None:
                return {}
            keep = (
                "intent_id", "source", "objective", "vertical", "kind",
                "regular", "stages", "reason", "text", "error",
            )
            return {k: data.get(k) for k in keep if k in data}
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _manager_intent_prompt_block(intent: dict[str, Any]) -> str:
        if not intent:
            return ""
        parts = [
            "## Manager intent boundary (authoritative)",
            f"- intent_id: {intent.get('intent_id') or ''}",
            f"- source: {intent.get('source') or ''}",
            f"- user_objective: {intent.get('objective') or ''}",
            f"- interpreted_vertical: {intent.get('vertical') or ''}",
            f"- kind: {intent.get('kind') or ''}",
            f"- stages: {', '.join(str(s) for s in (intent.get('stages') or []))}",
            f"- reason: {intent.get('reason') or intent.get('text') or ''}",
            "",
            "Plan only work consistent with this Manager boundary. If it appears "
            "wrong, surface a Manager/Planner mismatch instead of silently "
            "switching scope.",
        ]
        return "\n".join(parts)

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

    def _record_planner_waiting(self, verdict: Any, *, planner_cost_usd: float) -> str:
        sleep_s = self._enter_idle_backoff()
        reason = verdict.waiting_reason or verdict.reason or "awaiting external dependency"
        self._emit({
            "type": "life.planner.waiting",
            "cycle": self._planning_cycles,
            "reason": reason,
            "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
            "suggested_sleep_s": sleep_s,
            "input_tokens": getattr(verdict, "input_tokens", 0),
            "cached_input_tokens": getattr(verdict, "cached_input_tokens", 0),
            "output_tokens": getattr(verdict, "output_tokens", 0),
            "cost_usd": planner_cost_usd,
        })
        self._emit_status(f"awaiting external dependency: {reason}")
        return _PLAN_AWAITING

    def _maybe_dispatch_verification_probe(self, verdict: Any) -> bool:
        """Stall-breaker: after K consecutive idle cycles on the same external
        dependency, enqueue ONE domain-agnostic verification-probe mission so the
        agent TESTS its (possibly stale) belief against CURRENT reality.

        Returns True iff a probe was enqueued (caller runs it on the next tick).
        This does NOT judge the environment or override the planner's research
        judgment — it forces the agent to gather first-hand evidence so reality,
        not a memory of the blocker, drives the next decision.
        """
        n = int(getattr(self, "_consecutive_idle_planner_cycles", 0))
        if n < _VERIFICATION_PROBE_AFTER_IDLE_CYCLES:
            return False
        now = time.monotonic()
        if (now - getattr(self, "_last_verification_probe_at", 0.0)) < (
            _VERIFICATION_PROBE_COOLDOWN_SECONDS
        ):
            return False
        # Never stack a second probe while one is still pending/running.
        try:
            for it in self.memory.backlog.all():
                if "verification_probe" in (getattr(it, "tags", []) or []) and getattr(
                    it, "status", ""
                ) in ("pending", "running"):
                    return False
        except Exception:  # noqa: BLE001
            log.exception("verification-probe dedup scan failed; skipping probe")
            return False
        reason = (
            getattr(verdict, "waiting_reason", "")
            or getattr(verdict, "reason", "")
            or "an external dependency"
        )
        try:
            item = BacklogItem.new(
                title="verification probe: re-test the recorded external blocker",
                objective=(
                    "Verification-probe mission, dispatched by the harness after the "
                    f"planner idled {n} consecutive cycles concluding it was blocked. "
                    "Do NOT trust the journal's record of the blocker as still current. "
                    f'The recorded blocker was: "{reason}". RIGHT NOW, actually attempt '
                    "the blocked action — or run the single cheapest decisive probe of "
                    "it — and report the REAL present outcome with concrete first-hand "
                    "evidence (command output, file existence, an actual score/metric). "
                    "State plainly whether it is STILL blocked or has CLEARED. If it has "
                    "cleared, perform or unblock the smallest concrete next step. This is "
                    "a perception check, not make-work: completion is judged solely by "
                    "whether you produced fresh first-hand evidence of the blocker's "
                    "current state."
                ),
                priority=50,
                tags=["planner", "scope:bounded", "life", "verification_probe"],
                iterate=True,
                iteration_max_cycles=1,
                iteration_budget_usd=min(self._item_iteration_budget(), 5.0),
            )
            self.memory.backlog.add(item)
        except Exception:  # noqa: BLE001
            log.exception("failed to enqueue verification probe; continuing")
            return False
        self._last_verification_probe_at = now
        # Reset the idle counter so we don't immediately re-escalate before the
        # probe's real result lands in the event timeline (a real mission run
        # also resets it via _reset_idle_backoff()).
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        self._emit({
            "type": "life.planner.verification_probe",
            "cycle": self._planning_cycles,
            "reason": reason,
            "idle_cycles": n,
        })
        return True

    def _update_no_progress_streak(self, *, kind: str, report: Any) -> None:
        """Track consecutive 'completed but no forward progress' missions and,
        once the reviewer-judged streak crosses a threshold, emit an operator
        attention event (NOT a mission, NOT a verdict).

        Domain-agnostic by construction: it counts ONLY the L2 reviewer's own
        ``forward_progress`` boolean (agent judgment). The harness never decides
        what progress is — it only refuses to let the agent system do hollow work
        forever without surfacing the stall to its human operator. So a project
        that keeps completing no-score / blocked-archive refuges cannot loop
        invisibly: after N such missions the operator is pinged.
        """
        if kind != "mission_complete":
            return
        fp = report.get("forward_progress") if isinstance(report, dict) else None
        if fp is True:
            self._consecutive_no_progress_missions = 0
            return
        if fp is not False:
            return  # unknown / not reported — do not punish missing data
        n = int(getattr(self, "_consecutive_no_progress_missions", 0)) + 1
        self._consecutive_no_progress_missions = n
        if n < _STALL_ESCALATION_AFTER_NO_PROGRESS_MISSIONS:
            return
        # Threshold crossed: surface to the operator, then reset so the alert
        # re-fires after another N (not on every subsequent mission).
        self._consecutive_no_progress_missions = 0
        self._emit({
            "type": "life.planner.stall_escalation",
            "consecutive_no_progress_missions": n,
            "objective": (self.config.continuous_objective or "")[:200],
        })

    def _wiki_collect_task_if_due_under_blocker(self) -> Any | None:
        project_root = self._project_workdir()
        if not _operator_only_external_blocker_wait_reason_for_project(project_root):
            return None
        autors = project_root / ".autors"
        if not autors.is_dir():
            return None
        from datetime import datetime, timezone

        from ...planner import TaskSpec
        from ...wiki.bootstrap import is_initialized_wiki
        from ...wiki.bot_state import collect_cooldown_elapsed, load_bot_state

        now = datetime.now(timezone.utc)
        for candidate in sorted(autors.glob("*/wiki")):
            if not is_initialized_wiki(candidate):
                continue
            state = load_bot_state(candidate / "data" / "bot_state.json")
            if not collect_cooldown_elapsed(state=state, now=now):
                continue
            project_name = candidate.parent.name
            return TaskSpec(
                title=f"wiki_collect: refresh {project_name} idea wiki",
                objective=(
                    "wiki_collect mission. Use the `wiki-collector` engineer "
                    "skill to derive 5-10 project-state search queries, ingest "
                    "new paper/repo sources into `.autors/"
                    f"{project_name}/wiki/sources/`, and update "
                    "`data/bot_state.json`. This mission is allowed while the "
                    "project is externally blocked because it is train-free and "
                    "uses the shared per-mission budget. Do not run GPU work."
                ),
                impact_score=4,
                impact_area="discovery",
                evidence="collector cooldown elapsed while project waits on external artifacts",
                scope=_PLANNER_SCOPE_BOUNDED,
            )
        return None

    def _enqueue_wiki_collect_task(self, task: Any) -> bool:
        item = BacklogItem.new(
            title=task.title,
            objective=task.objective,
            priority=100,
            tags=[*self._planner_task_tags(task), "wiki_collect"],
            iterate=True,
            iteration_max_cycles=1,
            iteration_budget_usd=min(self._item_iteration_budget(), 5.0),
        )
        self.memory.backlog.add(item)
        self._emit({
            "type": "life.planner.task_added",
            "cycle": self._planning_cycles,
            "item_id": item.id,
            "title": item.title,
            "impact_score": task.impact_score,
            "impact_area": task.impact_area,
        })
        self._emit({
            "type": "life.planner.verdict",
            "cycle": self._planning_cycles,
            "project_done": False,
            "reason": "external blocker present; scheduling one wiki_collect escape-valve mission",
            "task_count": 1,
            "enqueued_tasks": 1,
            "skipped_duplicate_tasks": 0,
            "skipped_recent_failure_tasks": 0,
            "skipped_subagent_family_failure_tasks": 0,
            "enqueued_titles": [item.title],
            "enqueued_impact_scores": [task.impact_score],
            "skipped_duplicate_titles": [],
            "skipped_recent_failure_titles": [],
            "skipped_subagent_family_failure_titles": [],
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        })
        return True

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
            "type": "life.planner.start",
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
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": "no planner runner wired",
            })
            return None

        # Only skip the planner on an operator-only external blocker when the
        # full EMNLP gate is active. A ``--bounded`` mission
        # (``full_emnlp_gate=False``) does not require the external benchmark
        # targets, so it must fall through to the planner and reach its own
        # ``project_done`` instead of waiting forever on artifacts it never
        # needs. Mirrors the gating in
        # ``_defer_project_done_for_operator_external_blocker``.
        short_circuit = None
        if self._effective_full_emnlp_gate(self._artifact_root()):
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
                "type": "life.planner.error",
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
        )

        if verdict.error:
            self._emit({
                "type": "life.planner.error",
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
            and self._effective_full_emnlp_gate(self._artifact_root())
            and not self._journal_has_full_emnlp_gate_success()
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
                "type": "life.planner.verdict",
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
                "type": "life.planner.verdict",
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
                "type": "life.planner.verdict",
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
                "type": "life.planner.error",
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
                    "type": "life.planner.task_skipped",
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
                    "type": "life.planner.task_skipped",
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
                    "type": "life.planner.task_skipped",
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
                "type": "life.planner.task_added",
                "item_id": item.id,
                "title": item.title,
                "impact_score": task.impact_score,
                "impact_area": task.impact_area,
                "manager_intent": manager_intent,
            })

        self._emit({
            "type": "life.planner.verdict",
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

    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

    def _item_iteration_budget(self) -> float:
        """Default iteration budget for planner-generated tasks."""
        try:
            return max(0.0, float(self.config.planner_task_iteration_budget_usd))
        except (TypeError, ValueError):
            return 30.0

    def _render_journal_for_planner(self) -> str:
        """Render recent event-backed history for the planner's context."""
        try:
            entries = self.memory.journal.tail(20)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            from datetime import datetime
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            line = f"- [{ts}] {e.kind}: {e.title} — {e.summary}"
            extra = getattr(e, "extra", {}) or {}
            if isinstance(extra, dict):
                if extra.get("final_submission_certified"):
                    evidence = str(extra.get("completion_summary") or "").strip()
                    if evidence:
                        line += f" | final-submission evidence: {evidence[:500]}"
                if e.kind in ("mission_complete", "mission_failed"):
                    # Surface the L2 reviewer's own structured briefing so the
                    # planner attends to *what actually happened*, not just the
                    # `status=done` field. A mission can be marked done by being
                    # waved through a blocked/rollback/allowed-failure gate
                    # without resolving the underlying blocker; this report lets
                    # the planner avoid re-dispatching no-progress missions.
                    report = extra.get("planner_report")
                    if isinstance(report, dict) and report:
                        rendered = self._render_planner_report(report)
                        if rendered:
                            line += "\n" + rendered
                    feedback = extra.get("checklist_feedback")
                    if isinstance(feedback, dict) and feedback:
                        rendered_fb = self._render_checklist_feedback(feedback)
                        if rendered_fb:
                            line += "\n" + rendered_fb
                    step_back = extra.get("step_back")
                    if isinstance(step_back, dict) and step_back:
                        rendered_sb = self._render_step_back(step_back)
                        if rendered_sb:
                            line += "\n" + rendered_sb
            lines.append(line)
        return "\n".join(lines) or "(empty)"

    @staticmethod
    def _render_planner_report(report: dict) -> str:
        """Render the reviewer-authored planner briefing as plain lines.

        The reviewer authors a clean structured object; we only select and
        truncate its fields, never reformat free text or strip logs (the
        reviewer is instructed to emit clean content). Returns "" when the
        object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        forward = report.get("forward_progress")
        headline = _clean(report.get("headline"), 600)
        blocker = _clean(report.get("blocker"), 1200)
        recommended = _clean(report.get("recommended_next"), 1200)
        parts: list[str] = []
        if isinstance(forward, bool):
            parts.append(f"    reviewer→planner: forward_progress={forward}")
        if headline:
            parts.append(f"    headline: {headline}")
        if blocker:
            parts.append(f"    blocker: {blocker}")
        if recommended:
            parts.append(f"    recommended_next: {recommended}")
        evidence = report.get("evidence_files")
        if isinstance(evidence, list) and evidence:
            parts.append("    evidence_files the planner MUST open before replanning:")
            for entry in evidence[:8]:
                if not isinstance(entry, dict):
                    continue
                path = _clean(entry.get("path"), 400)
                if not path:
                    continue
                why = _clean(entry.get("why"), 600)
                parts.append(f"      - {path}" + (f"  — {why}" if why else ""))
        return "\n".join(parts)

    @staticmethod
    def _render_step_back(step_back: dict) -> str:
        """Render the reviewer's STEP-BACK reflection for the Planner.

        This is the anti-plan-lock-in block: a fresh-skeptic critique of THIS
        round's measured result, authored even on a clean success. The planner
        is REQUIRED (rule 17d) to triage each alt_direction. Returns "" when the
        object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        supported = _clean(step_back.get("supported_by_results"), 16)
        surprises = _clean(step_back.get("surprises"), 1200)
        parts: list[str] = [
            "    reviewer→planner STEP_BACK (anti-plan-lock-in — you MUST triage,"
            " rule 17d):"
        ]
        if supported:
            parts.append(f"      supported_by_results: {supported}")
        if surprises:
            parts.append(f"      surprises: {surprises}")
        questions = step_back.get("new_questions")
        if isinstance(questions, list) and questions:
            parts.append("      new_questions:")
            for q in questions[:5]:
                text = _clean(q, 400)
                if text:
                    parts.append(f"        - {text}")
        alts = step_back.get("alt_directions")
        if isinstance(alts, list) and alts:
            parts.append("      alt_directions (triage EACH — branch or reject with reason):")
            for entry in alts[:4]:
                if not isinstance(entry, dict):
                    continue
                direction = _clean(entry.get("direction"), 500)
                if not direction:
                    continue
                why = _clean(entry.get("why"), 500)
                cheap = bool(entry.get("cheap_to_test"))
                tag = " [cheap_to_test]" if cheap else ""
                parts.append(f"        - {direction}{tag}" + (f"  — {why}" if why else ""))
        # Header-only render carries no signal worth showing the planner.
        if len(parts) == 1:
            return ""
        return "\n".join(parts)

    @staticmethod
    def _render_checklist_feedback(feedback: dict) -> str:
        """Render the reviewer's ADVISORY checklist feedback for the Planner.

        The reviewer is feedback-only — it never edits the checklist. This block
        tells the Planner (the checklist OWNER) what to fix via ``checklist_ops``
        next cycle. Returns "" when the object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        stage = _clean(feedback.get("stage"), 100)
        summary = _clean(feedback.get("summary"), 600)
        parts: list[str] = []
        head = "    reviewer→planner CHECKLIST_FEEDBACK (you own the checklist — fix via checklist_ops)"
        if stage:
            head += f" [stage={stage}]"
        parts.append(head)
        if summary:
            parts.append(f"      summary: {summary}")
        items = feedback.get("items")
        if isinstance(items, list):
            for entry in items[:20]:
                if not isinstance(entry, dict):
                    continue
                problem = _clean(entry.get("problem"), 600)
                if not problem:
                    continue
                iid = _clean(entry.get("id"), 200)
                fix = _clean(entry.get("suggested_fix"), 600)
                label = f"      - {iid}: " if iid else "      - "
                parts.append(label + problem + (f"  → {fix}" if fix else ""))
        return "\n".join(parts) if len(parts) > 1 else ""
