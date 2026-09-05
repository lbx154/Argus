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
from ._evolution import EvolutionMixin
from ._idle_cycle import IdleCycleMixin, _idle_exit_seconds  # noqa: F401
from ._lifecycle import LifecycleMixin
from ._mission_execution import MissionExecutionMixin
from ._planner_orchestration import PlannerOrchestrationMixin
from ._planner_rendering import PlannerRenderingMixin
from ._planning_context import PlanningContextMixin
from ._planning_cycle import PlanningCycleMixin
from .pending_notify import should_report_pending_wait

log = logging.getLogger(__name__)

_price_for = price_for





# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------




# Plan-cycle outcome sentinels returned by ``_plan_next_work`` and consumed
# by ``run()``. Kept as a small named set (not bare string literals scattered
# across call sites) so the control flow stays auditable.
_PLAN_TASKS_ADDED = "tasks_added"
_PLAN_PROJECT_DONE = "project_done"

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
        self.manager = getattr(runner, "manager", None)
        self.sink = sink
        self.config = config or LifeSupervisorConfig()
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        # planner_runner: any RunnerBackend (codex / memory), used by the
        # continuous Planner. Result-shortfall iteration itself reuses the
        # normal Engineer/Reviewer path on the next supervisor tick.
        self.planner_runner = planner_runner
        # Optional role-scoped skill store for the planner mission matcher.
        # Threaded from the composition root (cockpit / life worker). None keeps
        # the planner on fixed role context only (no planner skill pool today).
        self.skill_store = skill_store
        self._missions_started = 0
        self._planning_cycles = 0
        # One-shot campaign route. Manager classifies the operator's initial
        # objective; Planner may select a mission-level vertical on later DAG
        # nodes without sending the original objective back through Manager.
        self._vertical_resolved = False
        # Idle backoff state (await-external / repeated no-work planner cycles).
        # Persists across daemon outer-loop iterations (the supervisor instance
        # is reused) so backoff escalates while the project waits, and resets
        # the moment a real mission runs.
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        self._parallel_plan_fingerprint: tuple[tuple[str, ...], ...] | None = None
        self._parallel_plan_after = 0.0
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

    def _bound_manager(self) -> Any:
        if self.manager is None:
            raise RuntimeError("LifeSupervisor requires a composed Manager")
        return self.manager.bind_execution_workdir(self._project_workdir())

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
                    EventType.LIFE_MISSION_REQUEUED
                    if requeued
                    else EventType.LIFE_MISSION_ORPHANED
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
            base = configured
        else:
            env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
            if env_workdir:
                base = Path(env_workdir).expanduser()
            else:
                project_root = getattr(self.memory, "project_root", None)
                if project_root:
                    base = Path(project_root)
                else:
                    project = getattr(self.memory, "project", None)
                    project_root = (
                        getattr(project, "root", None)
                        if project is not None
                        else None
                    )
                    base = Path(project_root) if project_root else Path(
                        getattr(self.memory, "root", None) or Path.cwd()
                    )
        try:
            from ...core.campaign_workdir import active_campaign_workdir

            active = active_campaign_workdir(self.memory.root, base)
            if active is not None:
                return active
        except Exception:  # noqa: BLE001 - invalid persisted adoption falls back
            log.debug("campaign workdir resolution failed", exc_info=True)
        return base

    def _artifact_root(self) -> Path:
        """Return the stable session root for Manager-owned harness state."""
        configured = getattr(self.config, "artifact_root", None)
        if configured is not None:
            return Path(configured).expanduser()
        project_state_dir = getattr(self.config, "project_state_dir", None)
        if project_state_dir is not None:
            return Path(project_state_dir).expanduser()
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
            from ...skills.stage_machine import current_stage

            return current_stage(root)
        except Exception:  # noqa: BLE001
            return None

    def _planner_workdir(self) -> Path:
        return self._project_workdir()

    def _planner_config(self):
        from ...core.knobs import resolve_role_model
        from ...daemon.state import read_continuous_state
        from ...planner import PlannerConfig

        continuous_root = Path(
            getattr(self.memory, "project_root", self.memory.root)
        )
        expected = read_continuous_state(continuous_root)

        def _semantic_interrupt() -> str | None:
            current = read_continuous_state(continuous_root)
            if (
                current.generation != expected.generation
                or current.enabled != expected.enabled
                or current.objective != expected.objective
            ):
                return "planner superseded by newer continuous generation"
            return None

        from ...core.role_session import objective_revision

        workdir = self._planner_workdir()
        state_root = self._artifact_root()
        try:
            from ...core.pipeline_state import read_pipeline_state

            pipeline = read_pipeline_state(state_root)
        except Exception:  # noqa: BLE001 - non-staged projects keep legacy behavior
            pipeline = None
        current_pipeline_stage = (
            str(pipeline.get("current_stage") or "").strip()
            if isinstance(pipeline, dict)
            and str(pipeline.get("vertical") or "").strip()
            else ""
        )
        return PlannerConfig(
            model=resolve_role_model("planner", role_env="ARGUS_SKILL_PLAN_MODEL")
            or self.reviewer_model,
            reasoning_effort=os.environ.get(
                "ARGUS_SKILL_PLANNER_REASONING_EFFORT", "high"
            ),
            working_dir=str(workdir),
            state_root=str(state_root),
            add_dirs=([str(state_root)] if state_root != workdir else []),
            skip_git_repo_check=True,
            dangerous_yolo=False,
            open_ended=bool(getattr(self.config, "open_ended", False)),
            external_interrupt_reason_provider=_semantic_interrupt,
            role_session_path=state_root / "role-sessions" / "planner.json",
            objective_revision=(
                f"{expected.generation}:"
                f"{objective_revision(expected.objective)}"
            ),
            on_event=getattr(self.sink, "handle_event", None),
            require_stage_decision=bool(current_pipeline_stage),
            current_stage=current_pipeline_stage,
        )

    # ------------------------------------------------------------------
    # Public driving methods
    # ------------------------------------------------------------------

    def _resume_automatic_pauses(self) -> list[BacklogItem]:
        """Wake pause classes whose external condition is rechecked per run.

        Operator pauses and scientific/infrastructure blocks stay explicit.
        Budget pauses wake only after the cheap global-cap preflight succeeds.
        """
        statuses = {
            "paused_provider_cooldown",
            "paused_provider_fence",
            "paused_daemon_shutdown",
        }
        try:
            budget_ok, _reason = self.config.budget.can_start(
                global_root=self._budget_global_root(),
            )
        except Exception:  # noqa: BLE001 - keep budget pauses conservative
            budget_ok = False
        if budget_ok:
            statuses.add("paused_budget")
        resumed = self.memory.backlog.resume_paused_statuses(statuses)
        from ...engineer.external_work import inspect_external_work

        # Subagent records are reconciled only by the interactive CLI listing,
        # so unattended nobody ever notices a job whose process is gone: run-01
        # held darc-v9-full915-claim-run-r2 as `running` for eight hours after
        # its pid died, and a paused mission kept reserving paths on its
        # behalf. Reconciling here costs a stat per record and is the same
        # check the CLI already makes.
        self._reconcile_dead_subagent_records()

        for item in self.memory.backlog.active():
            if item.status != "paused_external_work":
                continue
            wait = (
                item.outcome.get("external_wait")
                if isinstance(item.outcome, dict)
                else None
            )
            if not isinstance(wait, dict):
                ready = True
            else:
                work_id = str(wait.get("work_id") or "")
                workdir = Path(str(wait.get("workdir") or self._project_workdir()))
                status = inspect_external_work(workdir, work_id) if work_id else None
                ready = status is None or not status.waitable
            if ready:
                resumed_item = self.memory.backlog.resume_paused(item.id)
                if resumed_item is not None:
                    resumed.append(resumed_item)
        if resumed:
            self._emit_status(
                "auto-resumed recoverable mission(s): "
                + ", ".join(item.id for item in resumed)
            )
        return resumed

    def _reconcile_dead_subagent_records(self) -> None:
        """Mark subagent records whose process is gone, fail-soft throughout."""
        try:
            import json

            from ...tools.subagent._registry import reconcile_terminal_task

            root = (
                Path(self._project_workdir()).expanduser().resolve(strict=False)
                / ".argus_subagents"
            )
            if not root.is_dir():
                return
            for record_path in root.glob("*.json"):
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("state") not in {"starting", "preflight", "running"}:
                    continue
                before = record.get("state")
                task_id = str(record.get("task_id") or record_path.stem)
                updated = reconcile_terminal_task(
                    task_id,
                    dict(record),
                    registry_root=root,
                )
                if updated.get("state") != before:
                    self._emit_status(
                        f"external job {record_path.stem} is {updated.get('state')}: "
                        "its process is gone"
                    )
        except Exception:  # noqa: BLE001 - reconciliation never blocks work
            log.exception("life supervisor: subagent reconciliation failed")

    def _adjudicate_mission_challenge(self, outcome: dict[str, Any]) -> str:
        """Persist the Manager authority decision before Planner sees a challenge."""
        from ...manager import adjudicate_plan_challenge

        report = outcome.get("planner_report")
        challenge = dict(outcome.get("plan_challenge") or {})
        if not challenge:
            decision = adjudicate_plan_challenge(
                report if isinstance(report, dict) else {},
                reviewer_status=str(
                    outcome.get("review_status") or outcome.get("status") or ""
                ),
                review_reason=str(outcome.get("review_reason") or ""),
                next_action=str(outcome.get("stop_reason") or ""),
            )
            challenge = {
                "manager_action": decision.action,
                "manager_reason": decision.reason,
                "challenge": decision.challenge,
                "alternative": decision.alternative,
                "authority_impact": decision.authority_impact,
                "source": decision.source,
                "raised_at": time.time(),
            }
        now = time.time()
        try:
            raised_at = float(challenge.get("raised_at") or now)
        except (TypeError, ValueError):
            raised_at = now
        challenge["adjudicated_at"] = now
        challenge["revision_latency_seconds"] = max(0.0, now - raised_at)
        action = str(challenge.get("manager_action") or "revise").strip().lower()
        if action not in {"keep", "revise", "replace", "ask_operator"}:
            action = "revise"
        if action == "ask_operator":
            from ...manager.directive import active_operator_question_policy

            if active_operator_question_policy(self.memory.root) == "forbid":
                from ...core.autonomy import assess_operator_intervention

                reviewer_alternative = str(
                    challenge.get("alternative") or ""
                ).strip()
                boundary = assess_operator_intervention(
                    question=str(
                        challenge.get("operator_question")
                        or outcome.get("operator_question")
                        or challenge.get("challenge")
                        or outcome.get("review_reason")
                        or ""
                    ),
                    reason=str(challenge.get("challenge") or ""),
                    next_action=reviewer_alternative,
                    planner_report={},
                    mode="autonomous",
                )
                action = "blocked" if boundary.required else "revise"
                challenge["manager_reason"] = (
                    "Operator questions are forbidden and do not grant authority; "
                    + (
                        "no in-scope revision can cross this operator-owned boundary, "
                        "so the mission is blocked without a question."
                        if action == "blocked"
                        else "the Planner must revise strictly within existing "
                        "authority without using the Reviewer alternative."
                    )
                )
                challenge["authority_impact"] = "operator"
                challenge["alternative"] = ""
                challenge["operator_question"] = ""
                challenge["operator_options"] = []
                challenge.pop("pending_question", None)
                challenge.pop("operator_decision", None)
                outcome["operator_question"] = ""
                outcome["operator_options"] = []
                outcome.pop("pending_question", None)
                outcome.pop("operator_decision", None)
                if isinstance(report, dict):
                    report = dict(report)
                    report["authority_impact"] = "operator"
                    report.pop("alternative", None)
                    report.pop("operator_question", None)
                    report.pop("operator_options", None)
                    outcome["planner_report"] = report
        challenge["manager_action"] = action
        outcome["plan_challenge"] = challenge
        item_id = str(outcome.get("item_id") or "")
        self._emit({
            "type": EventType.LIFE_MANAGER_PLAN_CHALLENGE_DECIDED,
            "item_id": item_id,
            **challenge,
            "text": (
                f"Manager chose {action} after later evidence challenged the plan: "
                f"{str(challenge.get('challenge') or '')[:240]}"
            ),
        })
        if action == "ask_operator" and item_id:
            try:
                from ...core.operator_decision import build_operator_decision

                item = next(
                    row for row in self.memory.backlog.active() if row.id == item_id
                )
                question = (
                    "Please decide whether this operator-owned constraint may change: "
                    + str(challenge.get("challenge") or outcome.get("review_reason") or "")
                ).strip()
                card = build_operator_decision(
                    item_id=item.id,
                    title=item.title,
                    reason=str(challenge.get("manager_reason") or ""),
                    question=question,
                    project_id=self.memory.root.name,
                )
                self.memory.backlog.update(
                    item.id,
                    status="paused_operator",
                    pending_question=question,
                    operator_decision=card,
                )
                self._emit({
                    "type": EventType.LIFE_OPERATOR_QUESTION_PENDING,
                    "item_id": item.id,
                    "title": item.title,
                    "question": question,
                    "agent_layer": "manager",
                })
            except Exception:  # noqa: BLE001 - stop path still fails closed
                log.exception("failed to persist operator-owned plan challenge")
        elif action == "blocked" and item_id:
            reason = str(challenge.get("manager_reason") or "").strip()
            outcome["status"] = "blocked"
            outcome["review_status"] = "blocked"
            self.memory.backlog.update(
                item_id,
                status="failed",
                finished_ts=now,
                last_error=reason,
                pending_question="",
                operator_decision={},
            )
        return action

    def run(self) -> dict[str, Any]:
        """Drive missions until a stop condition. Returns a summary."""
        results: list[dict[str, Any]] = []
        stopped_by: str = ""
        self._resume_automatic_pauses()
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
                running_items = [
                    item
                    for item in self.memory.backlog.active()
                    if str(getattr(item, "status", "") or "") == "running"
                ]
                if running_items:
                    self._plan_alongside_running_work(running_items)
                    self._wait_idle()
                    continue
                # Backlog empty — continuous mode: ask planner for more
                if self.config.continuous and self.config.continuous_objective:
                    pending_questions = [
                        item
                        for item in self.memory.backlog.active()
                        if str(getattr(item, "pending_question", "") or "").strip()
                    ]
                    if pending_questions:
                        if self._resolve_pending_question_from_inbox(
                            pending_questions
                        ):
                            continue
                        # The question belongs to its item, not to the night.
                        # Stopping the whole campaign here cost run-06 about
                        # twenty hours and run-04 about twelve, each waiting on
                        # one answer with a half-written paper and plenty of
                        # independent work available. The paused item stays
                        # unclaimable and its dependents stay unready on their
                        # own; the Planner is told what is waiting and picks
                        # work that does not need the answer.
                        if should_report_pending_wait(
                            self.memory.root,
                            pending_questions,
                        ):
                            self._emit_status(
                                "Argus is waiting for your answer on: "
                                + "; ".join(
                                    str(item.pending_question).strip()
                                    for item in pending_questions[:3]
                                )
                                + " — independent work continues meanwhile"
                            )
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
                    # A bounded completion certificate is already a persisted
                    # Manager completion, so no new completion adjudication is
                    # needed and the Planner must not invent follow-up work.
                    bounded_completion = self._bounded_completion_reason()
                    if bounded_completion:
                        completion = self._emit_bounded_project_completion(
                            bounded_completion
                        )
                        if completion is False:
                            self._emit_status(
                                f"manager: project complete — {bounded_completion}"
                            )
                            stopped_by = "project_done"
                        else:
                            stopped_by = "planner_retry"
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
                parallel_worker = getattr(
                    self.config,
                    "parallel_worker",
                    False,
                )
                coordinate_claims = getattr(
                    self.config,
                    "coordinate_parallel_claims",
                    False,
                )
                next_item = (
                    self.memory.backlog.next_pending(
                        parallel_only=parallel_worker,
                        respect_running=coordinate_claims,
                    )
                    if parallel_worker or coordinate_claims
                    else self.memory.backlog.next_pending()
                )
                if next_item is None:
                    self._emit_status("backlog empty; exiting")
                    stopped_by = "backlog_empty"
                    break
                continue
            if outcome.get("status") != "claim_lost":
                results.append(outcome)
            if outcome.get("status") in {
                # Nothing ran and the item is still there, so re-selecting it
                # immediately just loses the claim again. run-07-panel emitted 154
                # of these in five minutes and run-02 784 in ten, at 84% CPU with
                # no model call for an hour, while the real evaluation it was
                # waiting on still had ninety minutes to run.
                "claim_lost",
                "paused_budget",
                "paused_provider_cooldown",
                "paused_provider_fence",
                "paused_daemon_shutdown",
                "paused_external_work",
                "paused_operator",
                "iteration_cap",
                "lifecycle_block",
                "stage_hold",
                "infra_blocked",
            }:
                # No mission actually ran — this is a held/paused outcome. Escalate
                # the wait like the idle path (15→300s) instead of resetting to
                # poll_interval, so a budget pause / F5 hold doesn't busy-spin and
                # re-flood the journal every 5s until the daily cap rolls over.
                if str(outcome.get("status") or "").startswith("paused_"):
                    self._enter_pause_backoff()
                else:
                    self._enter_idle_backoff()
                if outcome.get("status") == "claim_lost":
                    # The backoff only tells the daemon how long to sleep after
                    # run() returns; the loop would otherwise keep spinning
                    # inside this pass and reach for the same unclaimable item.
                    # Ending the pass is what makes the wait happen.
                    stopped_by = "claim_lost"
                    break
            else:
                # A real mission ran: clear any accumulated no-work backoff.
                self._reset_idle_backoff()
            # Auth failure flagged by _run_one: propagate immediately
            if outcome.get("auth_failure"):
                stopped_by = "auth_failure"
                break
            if outcome.get("status") == "replan_requested":
                manager_action = self._adjudicate_mission_challenge(outcome)
                if manager_action == "keep":
                    self._emit_status(
                        "Manager retained the current plan after reviewing the challenge"
                    )
                    continue
                if manager_action == "ask_operator":
                    self._emit_status(
                        "Manager held the challenged plan for an operator-owned decision"
                    )
                    stopped_by = "operator_decision_required"
                    break
                if manager_action == "blocked":
                    self._emit_status(
                        "Manager preserved the operator authority boundary and blocked "
                        "without asking"
                    )
                    stopped_by = "operator_authority_blocked"
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
                "paused_daemon_shutdown",
                "paused_operator",
                "iteration_cap",
                "lifecycle_block",
                "stage_hold",
                "infra_blocked",
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
            items = self.memory.backlog.active()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog after error")
            return []

        recovered: list[str] = []
        for item in items:
            if getattr(item, "status", "") != "running":
                continue
            owner = str(getattr(item, "running_owner", "") or "")
            worker_id = str(getattr(self.config, "worker_id", "primary") or "primary")
            if owner and owner != worker_id:
                continue
            if getattr(self.config, "parallel_worker", False) and owner != worker_id:
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
        parallel_worker = getattr(self.config, "parallel_worker", False)
        coordinate_claims = getattr(
            self.config,
            "coordinate_parallel_claims",
            False,
        )
        item = (
            self.memory.backlog.next_pending(
                parallel_only=parallel_worker,
                respect_running=coordinate_claims,
            )
            if parallel_worker or coordinate_claims
            else self.memory.backlog.next_pending()
        )
        if item is None:
            return None

        runtime_block = self._runtime_failure_circuit_block(item=item)
        if runtime_block is not None:
            return runtime_block

        obsolete_final_submission = (
            self._maybe_skip_inapplicable_final_submission_item(item)
        )
        if obsolete_final_submission is not None:
            return obsolete_final_submission

        budget_global_root = self._budget_global_root()
        ok, reason = self.config.budget.can_start(
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

        if (
            not self.config.continuous
            and self.config.budget.max_missions > 0
            and self._missions_started >= self.config.budget.max_missions
        ):
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

        mission_returned = False
        try:
            result = self._run_one(item)
            mission_returned = True
            return result
        finally:
            tags = {str(tag or "").strip().lower() for tag in item.tags}
            if "framework_maintenance" in tags:
                should_dispose = not mission_returned
                if mission_returned:
                    settled = next(
                        (
                            row
                            for row in reversed(self.memory.backlog.history())
                            if row.id == item.id
                        ),
                        item,
                    )
                    should_dispose = settled.status in {
                        "done",
                        "failed",
                        "aborted",
                        "skipped",
                        "superseded",
                    }
                if should_dispose:
                    from ._mission_execution_runtime import (
                        dispose_maintenance_worktree,
                    )

                    try:
                        dispose_maintenance_worktree(self.memory.root, item.id)
                    except (OSError, KeyError, RuntimeError, ValueError):
                        log.exception(
                            "life supervisor: maintenance worktree cleanup failed"
                        )
                    else:
                        self.memory.backlog.update(item.id, execution_workdir="")

    def _budget_global_root(self) -> Path:
        configured = getattr(self.memory, "global_root", None)
        if configured is not None:
            return Path(configured).expanduser()
        root = Path(getattr(self.memory, "root", ".")).expanduser()
        return root.parent.parent if root.parent.name == "projects" else root

    def _maybe_skip_inapplicable_final_submission_item(
        self,
        item: BacklogItem,
    ) -> dict[str, Any] | None:
        """Retire stale paper-final tasks when the active vertical is bounded.

        ``scope:final_submission`` only has meaning when the active vertical has
        a terminal gate that consumes it — either a certified completion gate or
        a required research target. If a stale default ``research`` state caused
        the planner to enqueue a final-submission proof for a Manager-authored
        bounded domain (for example ``perf_tuning``), do not spend another
        engineer/reviewer round proving the paper pipeline is missing. Mark the
        planner artifact ``skipped`` and let the bounded project reach its own
        terminal planner verdict.
        """
        if self._planner_scope_from_item(item) != _PLANNER_SCOPE_FINAL_SUBMISSION:
            return None
        if self._final_submission_scope_applies(self._artifact_root()):
            return None

        reason = (
            "skipped stale final_submission task: active vertical has no "
            "terminal certification gate"
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

    def _build_terminal_project_delivery(self, reason: str) -> dict[str, Any] | None:
        """Promote the last verified mission output only after project_done."""
        latest: dict[str, Any] = {}
        try:
            # Settlement-scoped tail: journal chatter (planner cycles, waiting
            # heartbeats) must not push the winning settlement out of view.
            # The ``success is True`` check stays literal: the kind projection
            # defaults a missing ``success`` to complete, and a delivery must
            # only ever promote an explicitly successful settlement.
            for entry in reversed(
                self.memory.journal.tail_settlements(
                    8, kinds=("mission_complete",)
                )
            ):
                extra = getattr(entry, "extra", None)
                if isinstance(extra, dict) and extra.get("success") is True:
                    latest = extra
                    break
        except Exception:  # noqa: BLE001 - delivery presentation is optional
            latest = {}
        try:
            from ..delivery import build_delivery_receipt

            outcome = latest.get("outcome")
            outcome = outcome if isinstance(outcome, dict) else {}
            candidates = latest.get("delivery_candidates")
            candidates = candidates if isinstance(candidates, list) else []
            project_id = Path(self.memory.root).name
            workspace = (
                str(latest.get("execution_workdir") or "").strip()
                or self._project_workdir()
            )
            final_submission_certified = bool(
                latest.get("final_submission_certified")
            )
            review_validity_message = ""
            if final_submission_certified:
                binding = latest.get("manuscript_snapshot")
                if isinstance(binding, dict):
                    from ...core.manuscript_snapshot import (
                        manuscript_review_status,
                    )

                    freshness = manuscript_review_status(
                        {"manuscript_snapshot": binding},
                        workspace,
                    )
                    if freshness.get("status") != "current":
                        final_submission_certified = False
                        review_validity_message = str(
                            freshness.get("message") or "stale manuscript review"
                        )
                else:
                    final_submission_certified = False
                    review_validity_message = (
                        "unbound (certification did not record the manuscript version)"
                    )
            receipt = build_delivery_receipt(
                item_id=f"project-{project_id}",
                title=(
                    str(getattr(self.config, "continuous_objective", "") or "").strip()
                    or str(latest.get("title") or "Completed task")
                ),
                summary=str(latest.get("summary") or reason or "").strip(),
                success=True,
                overall_complete=True,
                status="done",
                review_status=str(outcome.get("review_status") or "not_assessed"),
                final_submission_certified=final_submission_certified,
                workspace=workspace,
                state_root=self.memory.root,
                stage=str(self._current_pipeline_stage() or ""),
                reviewer_artifacts=candidates,
            )
            if review_validity_message:
                receipt["kind"] = "submission_stale"
                receipt["review_status"] = "stale"
                receipt["summary"] = review_validity_message
            return receipt
        except Exception:  # noqa: BLE001 - completion authority is unchanged
            log.debug("terminal project delivery could not be built", exc_info=True)
            return None

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
        if details.get("project_done") is True and "delivery" not in details:
            details["delivery"] = self._build_terminal_project_delivery(reason)
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
        if details.get("project_done") is True:
            report_result = self._manager_publish_project_report(reason)
            if report_result != "reported":
                self._emit({
                    "type": EventType.LIFE_PLANNER_ERROR,
                    "cycle": details.get("cycle", self._planning_cycles),
                    "error": (
                        "project completion was recorded, but the post-completion "
                        "Manager report is still pending"
                    ),
                    "reason": reason,
                    "delivery_id": event["delivery_id"],
                })
                return False
        try:
            from ...core.metrics import metrics_root_for_project, record_metric

            record_metric(
                metrics_root_for_project(self.memory.root),
                "goal.planning",
                labels={"status": str(status)},
                fields={
                    "delivery_id": event["delivery_id"],
                    "project_id": self.memory.root.name,
                    "project_done": bool(details.get("project_done", False)),
                    "task_count": int(details.get("task_count", 0) or 0),
                    "enqueued_tasks": int(details.get("enqueued_tasks", 0) or 0),
                    "skipped_duplicate_tasks": int(
                        details.get("skipped_duplicate_tasks", 0) or 0
                    ),
                },
            )
        except Exception:  # noqa: BLE001 - metrics never own planner delivery
            log.debug("goal planning metric skipped", exc_info=True)
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
                "type": "life.planner.verdict.discarded",
                "cycle": event.get("cycle", self._planning_cycles),
                "reason": "semantic state changed before the prior verdict was delivered",
                "delivery_id": record.get("delivery_id", ""),
            })
            self._emit_status("planner: ignored a stale verdict after newer project state")
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
        if event.get("project_done") is True:
            report_result = self._manager_publish_project_report(
                str(event.get("reason") or "")
            )
            if report_result != "reported":
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
        return True, outcome

    def _emit(self, event: dict[str, Any]) -> bool:
        try:
            accepted = self.sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: event sink raised")
            return False
        delivered = accepted is not False
        if delivered:
            event_type = str(event.get("type") or "")
            if event_type == EventType.LIFE_BUDGET_PAUSE:
                self._publish_budget_pause_message(event)
            elif event_type == EventType.LIFE_MISSION_COMPLETED:
                self._publish_mission_completion_message(event)
            elif (
                event_type == EventType.LIFE_PLANNER_VERDICT
                and event.get("project_done") is True
                and isinstance(event.get("delivery"), dict)
            ):
                delivery = dict(event["delivery"])
                self._publish_mission_completion_message({
                    "item_id": str(delivery.get("item_id") or "project"),
                    "title": str(delivery.get("title") or "Completed task"),
                    "success": True,
                    "status": "done",
                    "summary": str(delivery.get("summary") or event.get("reason") or ""),
                    "outcome": {
                        "review_status": str(
                            delivery.get("review_status") or "not_assessed"
                        ),
                    },
                    "final_submission_certified": (
                        delivery.get("kind") == "submission_certified"
                    ),
                    "overall_complete": True,
                    "campaign_continues": False,
                    "delivery": delivery,
                    "delivery_id": str(delivery.get("delivery_id") or ""),
                    "message_kind": "project-completed",
                })
        return delivered

    def _publish_mission_completion_message(self, event: dict[str, Any]) -> None:
        """Tell the operator a Team mission ended without another model call."""
        try:
            from ...core.operator_messages import (
                publish_operator_message,
                render_operator_update,
            )

            project = getattr(self.memory, "project", None)
            life_dir = getattr(project, "root", None) or getattr(self.memory, "root", None)
            if life_dir is None:
                return
            item_id = str(event.get("item_id") or "").strip()
            if not item_id:
                return
            from ...core.transcript import read_turns

            language_hint = next(
                (
                    str(turn.get("text") or "")
                    for turn in reversed(read_turns(life_dir, limit=20))
                    if turn.get("role") == "operator"
                ),
                "",
            )
            chinese = any("\u3400" <= char <= "\u9fff" for char in language_hint)
            title = str(event.get("title") or "Team mission").strip()
            success = bool(event.get("success"))
            summary = str(event.get("summary") or "").strip()
            from ...core.role_reply import strip_control_footer

            summary = strip_control_footer(
                summary,
                (
                    "MILESTONE_STATUS",
                    "RESULT",
                    "NEXT_OWNER",
                    "OPERATOR_QUESTION",
                    "OPERATOR_OPTIONS",
                ),
            )
            outcome = event.get("outcome")
            outcome = outcome if isinstance(outcome, dict) else {}
            review = str(outcome.get("review_status") or "").strip()
            independent_review_required = bool(
                event.get("independent_review_required")
            )
            delivery = (
                dict(event["delivery"])
                if isinstance(event.get("delivery"), dict)
                else None
            )
            final_submission_certified = (
                event.get("final_submission_certified") is True
            )
            if final_submission_certified:
                binding = event.get("manuscript_snapshot")
                if not isinstance(binding, dict) and isinstance(delivery, dict):
                    binding = delivery.get("manuscript_snapshot")
                if isinstance(binding, dict):
                    try:
                        from ...core.manuscript_snapshot import (
                            manuscript_review_status,
                        )

                        freshness = manuscript_review_status(
                            {"manuscript_snapshot": binding},
                            str(event.get("execution_workdir") or "").strip()
                            or self._project_workdir(),
                        )
                    except Exception:  # noqa: BLE001 - presentation fails closed
                        freshness = {
                            "status": "unbound",
                            "message": "unbound (certified manuscript cannot be read)",
                        }
                else:
                    freshness = {
                        "status": "unbound",
                        "message": (
                            "unbound (certification did not record the manuscript version)"
                        ),
                    }
                if freshness.get("status") != "current":
                    final_submission_certified = False
                    review = "stale"
                    summary = str(freshness.get("message") or "stale review")
                    if isinstance(delivery, dict):
                        delivery["kind"] = "submission_stale"
                        delivery["review_status"] = "stale"
                        delivery["summary"] = summary
            explicit_continuation = event.get("campaign_continues")
            campaign_continues = bool(
                success
                and (
                    explicit_continuation is True
                    or (
                        explicit_continuation is None
                        and bool(getattr(self.config, "continuous", False))
                        and not final_submission_certified
                    )
                )
            )
            delivery_ready = bool(
                delivery and isinstance(delivery.get("primary_target"), dict)
            )
            primary_target = (
                delivery.get("primary_target")
                if isinstance(delivery, dict)
                else None
            )
            delivery_path = (
                str(primary_target.get("path") or "").strip()
                if isinstance(primary_target, dict)
                else ""
            )
            delivery_line = (
                f"交付文件: {delivery_path}"
                if chinese and delivery_path
                else f"Deliverable: {delivery_path}"
                if delivery_path
                else ""
            )
            overall_complete = bool(
                success
                and not campaign_continues
                and (
                    event.get("overall_complete") is True
                    or final_submission_certified
                    or not bool(getattr(self.config, "continuous", False))
                )
            )
            summary_label = (
                "本次进展"
                if chinese and campaign_continues
                else "Progress"
                if campaign_continues
                else "本次完成"
                if chinese
                else "Mission summary"
            )
            summary_line = f"{summary_label}: {summary}" if summary else ""
            if success:
                if campaign_continues:
                    completion_label = "任务已继续" if chinese else "Task continued"
                elif overall_complete:
                    completion_label = (
                        "交付已认证"
                        if chinese and final_submission_certified
                        else "Submission certified"
                        if final_submission_certified
                        else "任务已完成"
                        if chinese
                        else "Task completed"
                    )
                else:
                    completion_label = "任务已结束" if chinese else "Task ended"
                result = (
                    completion_label
                    if chinese and overall_complete
                    else f"{completion_label} · {title}"
                )
                if (
                    review
                    and review not in {"none", "not_assessed"}
                    and not (overall_complete and independent_review_required)
                ):
                    result += f" · review={review}"
            else:
                status = str(event.get("status") or event.get("outcome_class") or "ended")
                reason = str(
                    event.get("stop_reason")
                    or event.get("failure_reason")
                    or "The mission ended without a verified result."
                ).strip()
                next_action = str(event.get("next_action") or "").strip()
                operator_question = str(
                    event.get("operator_question") or ""
                ).strip()
                from ...core.autonomy import assess_operator_intervention

                intervention = assess_operator_intervention(
                    question=(
                        operator_question
                        or next_action
                        if status in {"blocked", "paused_operator"}
                        else ""
                    ),
                    reason=reason,
                    next_action=next_action,
                    planner_report={
                        "authority_impact": (
                            "operator" if status == "paused_operator" else ""
                        )
                    },
                )
                if operator_question:
                    publish_operator_message(
                        life_dir,
                        text="\n".join(
                            part
                            for part in (title, summary_line, operator_question)
                            if part
                        ),
                        message_id=f"mission-result-{item_id}-{status}",
                        event_fields={
                            "mission_result": True,
                            "item_id": item_id,
                            "success": False,
                            "operator_question": operator_question,
                            "summary": summary,
                        },
                    )
                    return
                text = render_operator_update(
                    title=title,
                    status=status,
                    reason=reason,
                    next_action=next_action,
                    user_action_required=intervention.required,
                    language_hint=language_hint,
                )
                publish_operator_message(
                    life_dir,
                    text="\n".join(part for part in (text, summary_line) if part),
                    message_id=f"mission-result-{item_id}-{status}",
                    event_fields={
                        "mission_result": True,
                        "item_id": item_id,
                        "success": False,
                        "summary": summary,
                    },
                )
                return
            if campaign_continues:
                continuation = (
                    "任务已继续；Planner 正在选择下一步工作。"
                    if chinese
                    else "Task continues; Planner is selecting the next work item."
                )
            elif final_submission_certified:
                continuation = (
                    "最终交付已通过独立审核。"
                    if chinese
                    else "The final submission passed independent review."
                )
            elif overall_complete and independent_review_required and review == "done":
                continuation = (
                    "独立 Reviewer 已复核本轮产出，未发现阻断问题。"
                    if chinese
                    else (
                        "An independent Reviewer reviewed this run's output and "
                        "found no blocking issues."
                    )
                )
            elif str(outcome.get("stage_certification") or "").strip() == "deferred":
                continuation = (
                    "此工作项已完成；其他计划项仍在进行。"
                    if chinese
                    else (
                        "This work item is complete; other planned work is still "
                        "in progress."
                    )
                )
            elif overall_complete:
                continuation = (
                    "请求的工作已完成。"
                    if chinese
                    else "The requested work is complete."
                )
            else:
                continuation = (
                    "本次处理已结束，但没有可打开的交付成果。"
                    if chinese
                    else "This run ended without an openable deliverable."
                )
            publish_operator_message(
                life_dir,
                text="\n".join(
                    part
                    for part in (
                        result,
                        summary_line,
                        delivery_line,
                        continuation,
                    )
                    if part
                ),
                message_id=(
                    f"mission-result-{item_id}-"
                    f"{str(event.get('message_kind') or ('continued' if campaign_continues else 'completed' if overall_complete and delivery_ready else 'ended'))}"
                ),
                event_fields={
                    "mission_result": True,
                    "item_id": item_id,
                    "success": success,
                    "summary": summary,
                    "campaign_continues": campaign_continues,
                    "overall_complete": overall_complete,
                    "delivery": delivery if overall_complete and delivery_ready else None,
                    "delivery_id": (
                        str(delivery.get("delivery_id") or "")
                        if overall_complete and delivery_ready and delivery
                        else ""
                    ),
                },
            )
        except Exception:  # noqa: BLE001 - notification must not break supervision
            log.exception("life supervisor: failed to publish mission completion")

    def _publish_budget_pause_message(self, event: dict[str, Any]) -> None:
        """Surface a durable, deduplicated budget pause in the Manager chat."""
        try:
            from ...core.operator_messages import publish_operator_message, uses_cjk

            project = getattr(self.memory, "project", None)
            life_dir = getattr(project, "root", None) or getattr(self.memory, "root", None)
            if life_dir is None:
                return
            item_id = str(event.get("item_id") or "")
            title = str(event.get("title") or "current task").strip()
            reason = str(event.get("reason") or "budget cap reached").strip()
            chinese = uses_cjk(f"{title}\n{reason}")
            text = (
                f"项目已达到预算上限，任务已暂停：{title}。\n"
                "现有进度已保存；提高项目预算或缩小任务后即可继续。"
                if chinese
                else f"Paused because this project reached its budget limit: {title}.\n"
                "Existing work is saved; raise the project budget or narrow the task to continue."
            )
            publish_operator_message(
                life_dir,
                text=text,
                message_id=f"budget-pause-{item_id}-{reason}",
                event_fields={
                    "budget_pause": True,
                    "item_id": item_id,
                    "reason": reason,
                },
            )
        except Exception:  # noqa: BLE001 - alerting must not break supervision
            log.exception("life supervisor: failed to publish budget pause chat alert")

    def _plan_alongside_running_work(self, running_items: list[Any]) -> None:
        """Let the Planner fill an idle mission slot while other work runs.

        The primary supervisor is inside tick() driving the long mission, so the
        loop that reaches here is the parallel worker -- deliberately built with
        continuous=False and no objective, which is why the campaign's durable
        objective is adopted from the project life-dir rather than from config.

        Two things wedged this before. Asking once per set of running missions
        gave a six-hour job exactly one opportunity, at the moment it started,
        and a campaign whose paper sat untouched for twelve hours had already
        spent it. And skipping whenever anything was pending was wrong, because
        a pending item the parallel worker cannot claim -- not parallel_safe, or
        owning a path the running mission owns -- leaves the slot idle forever
        while looking like queued work. Only claimable work should suppress
        planning; the fingerprint carries the pending set so a task queued and
        not claimed does not immediately ask again, and the retry interval is
        the idle backoff the loop already uses.
        """
        try:
            from ...daemon.state import read_continuous_state

            durable = read_continuous_state(
                Path(getattr(self.memory, "project_root", self.memory.root))
            )
            objective = str(durable.objective or "").strip()
            if not (durable.enabled and objective):
                return
            self.config.continuous_objective = objective

            items = self.memory.backlog.active()
            # An operator question belongs to its item, not to the spare
            # campaign slot. The main idle path already plans around unanswered
            # questions; returning here reintroduced the same whole-campaign
            # stop whenever a long external job was also active.
            if self.memory.backlog.next_pending(parallel_only=True) is not None:
                return

            fingerprint = self._backlog_fingerprint(items, running_items)
            now = time.monotonic()
            if (
                fingerprint == self._parallel_plan_fingerprint
                and now < self._parallel_plan_after
            ):
                return
            self._parallel_plan_after = now + max(
                float(self.config.poll_interval_seconds),
                # Spare-slot planning throttles on idle-poll semantics, so it
                # deliberately stays on this cap rather than the operator-wait
                # turn re-grant constant that was split out of it.
                _IDLE_BACKOFF_CAP_SECONDS,
            )
            self._plan_next_work()
            # Re-read after planning so a task that was queued but cannot be
            # claimed counts as a change, and the next tick does not spin.
            self._parallel_plan_fingerprint = self._backlog_fingerprint(
                self.memory.backlog.active()
            )
        except Exception:  # noqa: BLE001 - filling a spare slot is best effort
            log.exception("life supervisor: parallel planning attempt failed")

    @staticmethod
    def _backlog_fingerprint(
        items: list[Any],
        running_items: list[Any] | None = None,
    ) -> tuple[tuple[str, ...], ...]:
        """Identify a backlog by its running and pending sets."""

        def _ids(rows: Any) -> tuple[str, ...]:
            return tuple(sorted(str(getattr(row, "id", "") or "") for row in rows))

        running = running_items if running_items is not None else [
            row for row in items
            if str(getattr(row, "status", "") or "") == "running"
        ]
        pending = [
            row for row in items
            if str(getattr(row, "status", "") or "") == "pending"
        ]
        return (_ids(running), _ids(pending))

    def _emit_status(self, text: str) -> None:
        self._emit({"type": "life.status", "text": text})
