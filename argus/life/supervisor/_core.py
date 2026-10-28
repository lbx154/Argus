"""Schedule missions and consume operator input for one persistent daemon.

The supervisor owns the outer loop, invokes the mission runner for each
claimed backlog item, and applies the mission ceiling and idle-backoff policy. Memory is
passed as separate context so it does not change the objective or mission ID.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from ...core.event_catalog import EventType
from ...core.handoff import holder_of
from ...core.mission_view import awaits_operator, say, sentences, uses_cjk
from ...core.planner_verdict import (
    PlannerVerdictStatus,
    build_planner_verdict_event,
)
from ...core.ports import EventSink
from ...core.stop_kinds import RUNTIME_RESUMED_PARKS
from ...core.usage import project_usage_summary
from ..memory import BacklogItem, shares_slot
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
    PLAN_AWAITING as _PLAN_AWAITING,
)
from ._constants import (
    PLAN_RETRY as _PLAN_RETRY,
)
from ._constants import (
    PLAN_TERMINAL_IDLE as _PLAN_TERMINAL_IDLE,
)
from ._constants import (
    PLANNER_SCOPE_FINAL_SUBMISSION as _PLANNER_SCOPE_FINAL_SUBMISSION,
)
from ._evolution import EvolutionMixin
from ._idle_cycle import IdleCycleMixin
from ._letters import LettersMixin
from ._lifecycle import LifecycleMixin
from ._mission_execution import MissionExecutionMixin
from ._planner_orchestration import PlannerOrchestrationMixin
from ._planner_rendering import PlannerRenderingMixin
from ._planning_context import PlanningContextMixin
from ._planning_cycle import PlanningCycleMixin
from .pending_notify import should_report_pending_wait

log = logging.getLogger(__name__)


# Outcomes produced by _plan_next_work and consumed by run().
_PLAN_TASKS_ADDED = "tasks_added"
_PLAN_PROJECT_DONE = "project_done"

def _review_phrase(review: str, chinese: bool) -> str:
    """A review outcome as a sentence that follows a completion headline.

    A passed review needs none: the completion already says so.
    """
    review = holder_of(review)
    if review in {"done", "passed", "approved"}:
        return ""
    known = review in {"engineer", "planner", "manager", "operator", "stale"}
    return say(f"review_{review}" if known else "review_other", chinese, review=review)


class LifeSupervisor(
    EvolutionMixin,
    LettersMixin,
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
      iteration cap is hit, or ``stop_event``
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
        engineer_model: str = "gpt-6.1-sol",
        reviewer_model: str = "gpt-6.1-sol",
        planner_runner: Any | None = None,
        skill_store: Any | None = None,
    ) -> None:
        self.memory = memory
        self.runner = runner
        self.manager = getattr(runner, "manager", None)
        from ...manager.supervision import SupervisionSink, start_supervision

        start_supervision(getattr(self.memory, "project_root", self.memory.root))
        from ...core.control_state_guard import orchestrate_state_root

        # Roles this supervisor starts may not change this project's control state.
        orchestrate_state_root(getattr(self.memory, "project_root", None) or getattr(self.memory, "root", None))
        from ..observer import ObserverSink

        self.sink = ObserverSink(SupervisionSink(sink, self), self)
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
        # Dependency keys the last planner DAG named that matched no backlog
        # item or durable job. They were dropped rather than rejecting the
        # plan; the next planner prompt says so once, then this clears.
        self._planner_dropped_dependency_keys: list[tuple[str, list[str]]] = []
        # Tasks whose parallel_safe flag was stripped at enqueue (missing or
        # invalid owns_paths, or stage-closing). Same one-shot contract as
        # the dropped-dependency note above.
        self._planner_dropped_parallel_marks: list[tuple[str, str]] = []
        # The facts the last spare-slot Planner call saw: the backlog's running
        # and pending sets and the Planner-visible input digest.
        self._parallel_plan_facts: tuple[Any, ...] | None = None
        # Parked cards the Manager could not yet decide in the operator's
        # place: attempts and when to ask again (_decide_parked_questions).
        self._proxy_retry: dict[str, tuple[int, float]] = {}
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
        # Planner idle log-hygiene (same family as the lifecycle-block
        # heartbeat above): suppress repeated identical planner_idle events.
        self._last_planner_idle_sig: str | None = None
        self._last_planner_idle_at = 0.0
        self._reap_orphans_on_startup()
        if self.manager is not None:
            from ...manager.supervision import recover_issued_supervision

            state_root = getattr(self.manager, "manager_session_root", None) or getattr(self.memory, "project_root", None) or self.memory.root
            recover_issued_supervision(self.manager, state_root)

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
            try:
                from ...core.runtime_incidents import (
                    RuntimeIncidentStore,
                    drain_runtime_incident_events,
                )

                store = RuntimeIncidentStore(self.memory.root)
                incident = store.detect(
                    detector="startup_orphan_reaper",
                    invariant="running_requires_live_executor",
                    subject_kind="mission",
                    subject_id=it.id,
                    severity="error",
                    observed={
                        "backlog_status": "running",
                        "daemon_restarted": True,
                        "orphan_retries": it.orphan_retries,
                    },
                )
                store.begin_recovery(
                    incident["incident_id"],
                    action="requeue_or_fail_orphaned_parent",
                    expected_postcondition=(
                        "orphaned parent is pending below the retry limit or failed "
                        "after the retry limit"
                    ),
                )
                current = next(
                    (
                        row
                        for row in self.memory.backlog.history()
                        if row.id == it.id
                    ),
                    None,
                )
                store.verify_recovery(
                    incident["incident_id"],
                    recovered=bool(
                        current is not None
                        and current.status in {"pending", "failed"}
                    ),
                    evidence={
                        "backlog_status": getattr(current, "status", ""),
                        "orphan_retries": getattr(current, "orphan_retries", 0),
                    },
                )
                drain_runtime_incident_events(
                    self.memory.root,
                    self.sink.handle_event,
                )
            except Exception:  # noqa: BLE001 - orphan recovery remains authoritative
                log.exception("life supervisor: orphan incident recording failed")

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

    def _project_state_root(self) -> Path:
        """The project's own state directory, never the shared global home.

        A split-memory daemon holds a ``MemoryBundle`` whose ``root`` is the
        global home, while the Manager-owned files a vertical lookup needs
        (pipeline state, delivery manifests, venue selection) live under its
        ``project_root``. A plain ``LifeMemory`` keeps both in one directory.
        """
        value = getattr(self.memory, "project_root", None) or getattr(
            self.memory, "root", None
        )
        return Path(value).expanduser() if value else self._artifact_root()

    def _current_pipeline_stage(self) -> str | None:
        """Read current stage through the active vertical contract.

        Do not trust raw ``PIPELINE_STATE.current_stage`` blindly: a project can
        carry an optimize vertical such as ``math_synth`` with a stale paper
        stage like ``research``. The stage-checklist helper clamps that to the
        vertical's first valid stage (``setup`` for the optimize verticals).
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
        from ...core.knobs import resolve_knob, resolve_role_model
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
        from ...skills.builtins import vertical_skill_read_dirs
        from ...skills.stage_machine import active_vertical_checklist_defs
        from ..knowledge_recall import knowledge_read_dirs

        # The stage prompt tells the read-only Planner to open the vertical's
        # packaged playbook; grant that directory, not the whole release. Its
        # recall points at libraries outside the workdir as well.
        playbook_dirs = vertical_skill_read_dirs(
            str(pipeline.get("vertical") or "").strip() if isinstance(pipeline, dict) else ""
        )
        recall_dirs = knowledge_read_dirs(
            self._project_state_root(), getattr(self.memory, "global_root", None),
            getattr(self.memory, "project_worktree", None),
        )
        return PlannerConfig(
            model=resolve_role_model("planner", role_env="ARGUS_SKILL_PLAN_MODEL")
            or self.reviewer_model,
            reasoning_effort=resolve_knob(
                "ARGUS_SKILL_PLANNER_REASONING_EFFORT", "high"
            ).value,
            working_dir=str(workdir),
            state_root=str(state_root),
            add_dirs=[*([str(state_root)] if state_root != workdir else []), *playbook_dirs, *recall_dirs],
            skip_git_repo_check=True,
            dangerous_yolo=False,
            open_ended=bool(self.config.continuous and self.config.open_ended),
            external_interrupt_reason_provider=_semantic_interrupt,
            role_session_path=state_root / "role-sessions" / "planner.json",
            on_event=getattr(self.sink, "handle_event", None),
            stages=tuple(active_vertical_checklist_defs(state_root)[0]) if current_pipeline_stage else (),
            route=tuple(self._waypoint_states()),
        )

    def _raise_waypoint_checkpoints(self) -> bool:
        """Park on the person's OK when a checkpoint step's work was delivered (``life.waypoint_cards``)."""
        from ..waypoint_cards import raise_due_checkpoints

        if getattr(getattr(self.memory, "backlog", None), "path", None) is None:
            return False
        try:
            return raise_due_checkpoints(self.memory.backlog, self._emit) > 0
        except Exception:  # noqa: BLE001 - the gate still holds later steps without the card
            log.exception("could not raise a required-step checkpoint")
            return False

    def _waypoint_states(self) -> list[Any]:
        """The person's required steps and where each stands (``core.waypoints``)."""
        from ...core.waypoints import read_step_states

        backlog = getattr(self.memory, "backlog", None)
        if getattr(backlog, "path", None) is None:
            return []
        try:
            return read_step_states(Path(backlog.path).parent, backlog.history())
        except Exception:  # noqa: BLE001 - a damaged canvas must not stop planning
            log.exception("required steps could not be read")
            return []

    # ------------------------------------------------------------------
    # Public driving methods
    # ------------------------------------------------------------------

    def _resume_automatic_pauses(self) -> list[BacklogItem]:
        """Re-arm the parks the runtime itself ends on a new pass.

        Provider fences, operator pauses, and research/infrastructure parks
        wait for someone: time passing does not establish quota recovery.
        """
        resumed = self.memory.backlog.resume_parked(RUNTIME_RESUMED_PARKS)
        if resumed:
            self._emit_status(
                "auto-resumed recoverable mission(s): "
                + ", ".join(item.id for item in resumed)
            )
        return resumed

    def run(self) -> dict[str, Any]:
        """Drive missions until a stop condition. Returns a summary."""
        try:
            return self._run_with_inbox()
        finally:
            self._release_operator_inbox()

    def _run_with_inbox(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        stopped_by: str = ""
        # With several mission workers the dispatcher resumes paused missions
        # once per cycle before any worker claims, so a resumed mission is
        # claimable by whichever worker is free, not only by the resumer.
        if not getattr(self, "_dispatcher_resumes", False):
            self._resume_automatic_pauses()
        while True:
            self._drain_peer_inbox()
            if not self._drain_mission_completions():
                stopped_by = "mission_delivery_pending"
                self._suggested_sleep_s = 1.0
                break
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
                if self._raise_waypoint_checkpoints():
                    continue
                if self._decide_parked_questions():
                    continue
                active_items = self.memory.backlog.active()
                running_items = [
                    item
                    for item in active_items
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
                        # work that does not need the answer. While the
                        # operator cannot be asked, the Manager answers instead.
                        from ...core.autonomy import operator_questions_can_be_asked

                        if operator_questions_can_be_asked(
                            self._project_state_root()
                        ) and should_report_pending_wait(
                            self.memory.root,
                            pending_questions,
                        ):
                            asked = "; ".join(
                                str(item.pending_question).strip()
                                for item in pending_questions[:3]
                            )
                            self._tell_status("waiting_for_answer", question=asked)
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
                    log.info("stop requested while idle")
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
                    log.info("backlog empty; exiting")
                    stopped_by = "backlog_empty"
                    break
                continue
            if outcome.get("status") == "mission_delivery_pending":
                stopped_by = "mission_delivery_pending"
                self._suggested_sleep_s = 1.0
                break
            if outcome.get("status") != "claim_lost":
                results.append(outcome)
            if outcome.get("status") in {
                # Nothing ran and the item is still there, so re-selecting it
                # immediately just loses the claim again. run-07-panel emitted 154
                # of these in five minutes and run-02 784 in ten, at 84% CPU with
                # no model call for an hour, while the real evaluation it was
                # waiting on still had ninety minutes to run.
                "claim_lost",
                "parked",
                "iteration_cap",
                "lifecycle_block",
                "stage_hold",
                "runtime_block",
            }:
                # No mission actually ran — this is a held/paused outcome. Escalate
                # the wait like the idle path (15→300s) instead of resetting to
                # poll_interval, so a budget pause / F5 hold doesn't busy-spin and
                # re-flood the journal every 5s until the daily cap rolls over.
                if outcome.get("status") == "parked":
                    self._enter_pause_backoff()
                else:
                    self._enter_idle_backoff()
                # Long waits are when the operator most wants a word.
                self._maybe_write_letter()
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
            if outcome.get("status") == "planner":
                # A Reviewer's challenge is the Planner's input as it stands:
                # the Planner owns the plan, so nobody adjudicates it first.
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
                "parked",
                "iteration_cap",
                "lifecycle_block",
                "stage_hold",
                "runtime_block",
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
        self._drain_peer_inbox()
        if not self._drain_mission_completions():
            return {"status": "mission_delivery_pending", "recoverable": True}
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

    def _drain_mission_completions(self) -> bool:
        """Recover completion delivery before planning or claiming more work."""
        from ..mission_delivery import drain_mission_deliveries

        stop_event = getattr(self.config, "stop_event", None)
        if stop_event is not None and stop_event.is_set():
            return True
        backlog = getattr(self.memory, "backlog", None)
        if not callable(getattr(backlog, "pending_mission_deliveries", None)):
            return True
        try:
            return drain_mission_deliveries(backlog, self._emit, self._confirm_mission_completion_receipt)
        except Exception:
            log.exception("mission completion recovery is awaiting durable storage")
            return False

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
        settlements = []
        try:
            # Settlement-scoped tail: journal chatter (planner cycles, waiting
            # heartbeats) must not push the winning settlement out of view.
            # The ``success is True`` check stays literal: the kind projection
            # defaults a missing ``success`` to complete, and a delivery must
            # only ever promote an explicitly successful settlement.
            settlements = self.memory.journal.tail_settlements(8, kinds=("mission_complete",))
            for entry in reversed(settlements):
                extra = getattr(entry, "extra", None)
                if isinstance(extra, dict) and extra.get("success") is True:
                    latest = extra
                    break
        except Exception:  # noqa: BLE001 - delivery presentation is optional
            latest = {}
        try:
            from ..delivery import build_delivery_receipt, declared_source

            outcome = latest.get("outcome")
            outcome = outcome if isinstance(outcome, dict) else {}
            project_id = Path(self.memory.root).name
            workspace = (
                str(latest.get("execution_workdir") or "").strip()
                or self._project_workdir()
            )
            objective = str(getattr(self.config, "continuous_objective", "") or "").strip()
            goal_items = {
                item.id for item in self.memory.backlog.all()
                if item.status == "done" and objective
                and str(item.original_objective or item.objective).strip() == objective
            }
            task_delivery = latest.get("delivery")
            if (
                isinstance(task_delivery, dict) and task_delivery.get("kind") == "task_completed"
                and goal_items <= {latest.get("item_id")}
            ):
                # The project was this one task: its receipt is the project's,
                # with the same primary file and the same identity.
                return dict(task_delivery)
            # The files the last settlement declared, then those of earlier
            # nodes in this same goal.
            declared = [latest]
            for entry in reversed(settlements):
                extra = getattr(entry, "extra", None)
                if (
                    isinstance(extra, dict) and extra.get("success") is True
                    and extra.get("item_id") in goal_items
                    and str(extra.get("execution_workdir") or workspace) == str(workspace)
                ):
                    declared.append(extra)
            candidates = [
                (path, declared_source(extra.get("review_source")))
                for extra in declared for path in extra.get("delivery_candidates") or ()
            ]
            from ...skills.vertical_select import resolve_vertical_if_decided

            if resolve_vertical_if_decided(self._artifact_root()) == "software":
                # Keep the product and report ahead of implementation sources
                # when the bounded delivery list is clipped.
                presentation_order = {".html": 0, ".pdf": 1, ".md": 2, ".markdown": 2, ".csv": 3, ".tsv": 3}
                candidates.sort(key=lambda row: presentation_order.get(Path(row[0]).suffix.lower(), 4))
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
            if final_submission_certified and resolve_vertical_if_decided(self._artifact_root()) == "research":
                from types import SimpleNamespace

                from ...core.venue_review import current_venue_acceptance_issue

                acceptance_issue = current_venue_acceptance_issue(
                    SimpleNamespace(
                        venue_review=latest.get("venue_review"),
                        venue_review_snapshot=latest.get("venue_review_snapshot"),
                        review_source="reviewer",
                    ),
                    state_root=self._project_state_root(), artifact_root=workspace,
                )
                if acceptance_issue:
                    final_submission_certified = False
                    review_validity_message = acceptance_issue
            receipt = build_delivery_receipt(
                # A session can complete several goals. The final settled task
                # makes each delivery new while reconnects stay idempotent.
                item_id=f"project-{project_id}-{latest.get('item_id') or 'complete'}",
                title=(
                    str(getattr(self.config, "continuous_objective", "") or "").strip()
                    or str(latest.get("title") or "Completed task")
                ),
                summary=str(latest.get("summary") or latest.get("final_output") or reason or "").strip(),
                success=True,
                overall_complete=True,
                status="done",
                review_status=str(outcome.get("review_status") or "not_assessed"),
                review_source=str(latest.get("review_source") or ""),
                final_submission_certified=final_submission_certified,
                workspace=workspace,
                state_root=self._project_state_root(),
                stage=str(self._current_pipeline_stage() or ""),
                deliverables=candidates,
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
        event["delivery_id"] = planner_verdict_delivery_id()
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
        handled_revision = int(event.get("handled_operator_context_revision") or 0)
        if completion_kind == "certified_increment" and handled_revision > 0:
            # The validated handoff is now a durable decision, even if report
            # delivery fails below. Checkpoint only the input Planner actually
            # handled so retry can drain the outbox, not repeat planning.
            from ...core.operator_context import OperatorContextStore, operator_context_state_root

            try:
                settled = OperatorContextStore(operator_context_state_root(self.memory)).acknowledge(
                    "planner", handled_revision,
                    settle=event.get("settled_operator_instructions") or (),
                )
                if settled:
                    log.info("Planner settled idle operator instructions %s", list(settled))
            except (OSError, ValueError):
                log.exception("failed to checkpoint certified Planner handoff")
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
        if details.get("project_done") is True or completion_kind == "certified_increment":
            self._manager_publish_project_report(
                reason,
                delivery=event.get("delivery"),
                **(
                    {"certified_increment": True}
                    if completion_kind == "certified_increment"
                    else {}
                ),
            )
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
        if event.get("project_done") is True or event.get("completion_kind") == "certified_increment":
            self._manager_publish_project_report(
                str(event.get("reason") or ""),
                delivery=event.get("delivery"),
                **(
                    {"certified_increment": True}
                    if event.get("completion_kind") == "certified_increment"
                    else {}
                ),
            )
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
            if (
                event_type == EventType.LIFE_MISSION_COMPLETED
                and event.get("certification_recovered") is not True
            ):
                self._publish_mission_completion_message(event)
                # A mission boundary is a natural moment to write home.
                self._maybe_write_letter()
        # A finished project is told by its completion message; its verdict
        # carries the delivery card, not a second completion message.
        return delivered

    def _continuation_line(self, item_id: str, chinese: bool) -> str:
        """Say what happens after a finished task, naming the next queued item.

        Each continued task used to end with the same generic sentence, so a
        long campaign showed it a dozen times in a row. Reading the backlog
        here keeps the line specific to this moment.
        """
        try:
            pending = [row for row in self.memory.backlog.pending() if row.id != item_id]
        except Exception:  # noqa: BLE001 - a status line must not break settlement
            pending = []
        if not pending:
            return say("nothing_queued", chinese)
        from ...core.operator_messages import brief_title

        more, done = len(pending) - 1, self._done_task_count()
        return sentences(
            chinese,
            say(
                "next_up_and_more" if more > 1 else "next_up_and_one" if more else "next_up",
                chinese, title=brief_title(pending[0].title) or say("next_item", chinese), more=more,
            ),
            say("done_so_far", chinese, done=done) if done else "",
        )

    def _done_task_count(self) -> int:
        try:
            return sum(1 for row in self.memory.backlog.history() if str(row.status) == "done")
        except Exception:  # noqa: BLE001 - a status line must not break settlement
            return 0

    def _question_waits(self) -> bool:
        try:
            return awaits_operator(vars(row) for row in self.memory.backlog.active())
        except Exception:  # noqa: BLE001 - a status line must not break settlement
            return False

    def _publish_mission_completion_message(self, event: dict[str, Any]) -> None:
        """Tell the operator a Team mission ended without another model call."""
        try:
            from ...core.operator_messages import (
                brief_title,
                render_operator_update,
            )

            project = getattr(self.memory, "project", None)
            life_dir = getattr(project, "root", None) or getattr(self.memory, "root", None)
            if life_dir is None:
                return
            item_id = str(event.get("item_id") or "").strip()
            if not item_id:
                return
            stable_delivery_id = str(event.get("mission_delivery_id") or "")

            def _message_id(legacy: str) -> str:
                return f"mission-result-{stable_delivery_id}" if stable_delivery_id else legacy

            from ...core.transcript import operator_request

            language_hint = operator_request(life_dir)
            chinese = uses_cjk(language_hint)
            title = str(event.get("title") or say("this_task", chinese)).strip()
            success = bool(event.get("success"))
            summary = str(event.get("summary") or "").strip()
            outcome = event.get("outcome")
            outcome = outcome if isinstance(outcome, dict) else {}
            status = str(event.get("status") or event.get("outcome_class") or "ended")
            if status == "stage_continues":
                self._show_team_message(
                    life_dir,
                    text=render_operator_update(
                        title=title, status=status,
                        reason=str(event.get("stop_reason") or ""),
                        next_action=say("will_continue", chinese),
                        language_hint=language_hint,
                    ),
                    message_id=_message_id(f"mission-continued-{item_id}"),
                    event_fields={
                        "mission_result": True, "item_id": item_id, "status": status,
                        "success": success, "campaign_continues": True,
                        "overall_complete": False, "user_action_required": False,
                    },
                )
                return
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
            # A reviewed interim package is delivered while the campaign goes
            # on; it must never read as the finished goal.
            stage_package = bool(delivery_ready and delivery.get("kind") == "stage_package")
            delivery_line = delivery_path and say(
                "interim_result_in" if stage_package else "result_in", chinese, path=delivery_path,
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
            brief = brief_title(title)
            if not success:
                # A role that hands the work on gives its reason in its own summary.
                reason = str(
                    event.get("stop_reason") or event.get("failure_reason") or summary
                ).strip()
                next_action = str(event.get("next_action") or "").strip()
                operator_question = str(
                    event.get("operator_question") or ""
                ).strip()
                park = str(event.get("park_reason") or "") if status == "parked" else ""
                if operator_question:
                    from .pending_notify import numbered_options

                    # The answers the task offers, as its card shows them.
                    asking = next((row for row in self.memory.backlog.active() if row.id == item_id), None)
                    decision = getattr(asking, "operator_decision", None)
                    self._show_team_message(
                        life_dir,
                        text="\n".join(part for part in (
                            say("needs_your_call", chinese, title=brief, question=operator_question),
                            numbered_options(decision.get("options") if isinstance(decision, dict) else None,
                                             chinese),
                            summary if summary != operator_question else "",
                        ) if part),
                        message_id=_message_id(f"mission-result-{item_id}-{status}"),
                        event_fields={
                            "mission_result": True,
                            "item_id": item_id,
                            "success": False,
                            "operator_question": operator_question,
                            "summary": summary,
                        },
                    )
                    return
                # Work parked on the operator waits for their decision; work
                # handed to the Manager is the Manager's to decide, as the
                # settlement parks it and supervision puts it to the Manager.
                to_manager = holder_of(review) == "manager"
                text = render_operator_update(
                    title=title,
                    status=status,
                    park_reason=park,
                    reason=reason,
                    next_action=next_action,
                    holder=(
                        "operator" if park in {"operator_question", "operator_pause"}
                        else "manager" if to_manager
                        else "planner" if status == "planner" else ""
                    ),
                    language_hint=language_hint,
                )
                # The summary often is the stop reason, its lines run together;
                # the update gives its first sentence, its row the rest.
                told = " ".join(summary.split()) in " ".join(f"{reason} {text}".split())
                self._show_team_message(
                    life_dir,
                    text="\n".join(part for part in (text, "" if told else summary) if part),
                    message_id=_message_id(f"mission-result-{item_id}-{status}"),
                    event_fields={
                        "mission_result": True,
                        "item_id": item_id,
                        "success": False,
                        "summary": summary,
                    },
                )
                return
            reviewed = event.get("review_source") == "reviewer"
            reviewer_found_nothing = bool(
                overall_complete and independent_review_required and review == "done" and reviewed
            )
            # The item is done while other planned work goes on, or while
            # another task waits on the operator's answer.
            question_waits = not campaign_continues and self._question_waits()
            deferred = question_waits or (
                not campaign_continues
                and str(outcome.get("stage_certification") or "").strip() == "deferred"
            )
            if final_submission_certified:
                headline = say("final_submission_ready", chinese)
            elif overall_complete or deferred:
                done_kind = "item_done" if deferred else "all_done"
                headline = say(
                    done_kind + "_reviewed" if reviewer_found_nothing else done_kind, chinese,
                    rest=say("rest_awaits_answer" if question_waits else "rest_under_way", chinese),
                )
            else:
                headline = say("step_done" if campaign_continues else "task_ended", chinese, title=brief)
            if (
                review not in {"", "none", "not_assessed", "done"}
                and not final_submission_certified
                and not (overall_complete and independent_review_required)
            ):
                headline = sentences(chinese, headline, _review_phrase(review, chinese))
            if campaign_continues:
                continuation = self._continuation_line(item_id, chinese)
            elif overall_complete or deferred:
                continuation = ""
            else:
                continuation = say("no_file_produced", chinese)
            from ...core.autonomy import render_autonomous_assumptions

            # What this task decided in the operator's place belongs in its
            # completion message, not only in the event log.
            assumptions_line = render_autonomous_assumptions(
                self._project_state_root(), item_id=item_id, chinese=chinese,
            )
            self._show_team_message(
                life_dir,
                text="\n".join(
                    part
                    for part in (
                        headline,
                        summary,
                        delivery_line,
                        continuation,
                        assumptions_line,
                    )
                    if part
                ),
                message_id=_message_id(
                    f"mission-result-{item_id}-"
                    f"{'continued' if campaign_continues else 'completed' if overall_complete and delivery_ready else 'ended'}"
                ),
                event_fields={
                    "mission_result": True,
                    "item_id": item_id,
                    "success": success,
                    "summary": summary,
                    "campaign_continues": campaign_continues,
                    "overall_complete": overall_complete,
                    "delivery": (
                        delivery
                        if delivery_ready and (overall_complete or stage_package)
                        else None
                    ),
                    "delivery_id": (
                        str(delivery.get("delivery_id") or "")
                        if delivery_ready and (overall_complete or stage_package) and delivery
                        else ""
                    ),
                },
            )
        except Exception:  # noqa: BLE001 - notification must not break supervision
            log.exception("life supervisor: failed to publish mission completion")

    def _confirm_mission_completion_receipt(self, event: dict[str, Any]) -> bool:
        """Put the result in the chat before acknowledging completion.

        The report's message id makes retry safe even when the process died
        after leaving it. External letters/notifications remain separate.
        """
        from ...core.team_reports import team_message_told as _told

        delivery_id = str(event.get("mission_delivery_id") or "")
        if not delivery_id:
            return True
        project = getattr(self.memory, "project", None)
        root = Path(getattr(project, "root", None) or self.memory.root)
        message_id = f"mission-result-{delivery_id}"
        if not _told(root, message_id):
            self._publish_mission_completion_message(event)
        return _told(root, message_id)

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
        planning. And where a claim waits on running work (several slots),
        nothing starts beside work that does not share its slot: asked as such
        a task it had just queued started (2026-10-22), the Planner could only
        say "nothing new".

        The Planner is asked again only when a fact it reads has changed: the
        running or pending set, or anything in its input digest (settled
        missions, background-job states, operator context, Manager feedback).
        A timer re-asked it every few minutes for the whole of a long job and
        nearly every answer was "still waiting"; in recorded campaigns, every
        re-ask that did schedule work came right after one of those facts moved.
        """
        try:
            from ...daemon.state import read_continuous_state

            gated = self.config.parallel_worker or self.config.coordinate_parallel_claims
            if gated and not all(map(shares_slot, running_items)):
                return
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

            facts = (
                self._backlog_fingerprint(items, running_items),
                self._current_planner_input_signature(),
            )
            if facts == self._parallel_plan_facts:
                return
            self._plan_next_work()
            # Re-read after planning so the call's own writes (a task queued but
            # not claimable) are what the next tick compares against.
            self._parallel_plan_facts = (
                self._backlog_fingerprint(self.memory.backlog.active(), running_items),
                self._current_planner_input_signature(),
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
        """One line of the developer log; screens hide it by default."""
        self._emit({"type": "life.status", "text": text})

    def _operator_words(self, kind: str, **params: Any) -> dict[str, str]:
        """A kind and its sentence in the operator's language, for an event they read."""
        return {"kind": kind, "text": say(kind, self._operator_chinese(), **params)}

    def _tell_status(self, kind: str, **params: Any) -> None:
        """A status line the operator reads, unlike the developer log."""
        self._emit({"type": EventType.LIFE_OPERATOR_STATUS, **self._operator_words(kind, **params)})

    def _operator_chinese(self) -> bool:
        """Whether the operator writes Chinese, as their objective reads."""
        return uses_cjk(getattr(self.config, "continuous_objective", ""))

    def _show_team_message(
        self, life_dir: Path | str, *, text: str, message_id: str, event_fields: dict[str, Any],
    ) -> None:
        """Tell the operator what the team has to say, in its chat, as it happens.

        A Manager turn on each report first waited for the web server to pick
        it up, then for a model call: s-ec08e4a5's result reached the chat
        4.7 minutes after the task finished (10-24). The Manager reads it at
        the start of its next turn.
        """
        from ...core.team_reports import show_team_message

        show_team_message(Path(life_dir), text=text, message_id=message_id, event_fields=event_fields)

    def _tell_operator(self, notice_id: str, kind: str, **params: Any) -> None:
        """Post the sentence for ``kind`` to the operator's conversation, once per ``notice_id``."""
        from ...core.operator_messages import publish_operator_message

        try:
            publish_operator_message(
                self._manager_project_report_root(),
                text=say(kind, self._operator_chinese(), **params),
                message_id=f"argus-notice-{notice_id}",
                event_fields={"operator_notice": notice_id},
            )
        except Exception:  # noqa: BLE001 - the status line below still records it
            log.warning("could not post operator notice %s", notice_id, exc_info=True)
        self._emit_status(say(kind, False, **params))
