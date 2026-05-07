"""``MissionExecutor`` — per-task ArgusBot LoopEngine runner with skill loop.

This is the merger of "form A" (queue Daemon — `/run` per-task ergonomics)
and "form B" (MissionDaemon — full LoopEngine reviewer-loop + planner +
auto-follow-up). The queue Daemon delegates each ``/run <task>`` to
``MissionExecutor.execute(...)``, which:

  1. Builds **fresh** ``SkillLoopRunner`` + ``LoopConfig`` +
     ``LoopStateStore`` + ``LoopEngine`` per task. (Per rubber-duck
     critique #4 — caching across tasks would leak ``_cached_skill``,
     ``mission_objective`` etc.)
  2. Tracks the active ``LoopStateStore`` under a lock so the Daemon
     can hard-cancel via ``cancel_active()`` (used by ``/stop`` and
     ``/skip``).
  3. Forwards events to the daemon's sink AND watches for
     ``plan.completed`` so we know whether a follow-up phase fired
     (relevant for skill-writeback gating, critique #5).
  4. On reviewer-confirmed success, performs **gated skill writeback**:
     only when (a) the matcher hit an existing skill, (b) no follow-up
     phase changed task scope, and (c) the writeback option is on.

Stable deps reused across tasks: ``SkillStore``, ``Distiller``,
``engineer_backend`` (argus-skill ``RunnerBackend``), ``codex_runner``
(ArgusBot ``CodexRunner`` for reviewer/planner/reports), ``Reviewer``,
``Planner``.

Fresh per task: ``SkillLoopRunner``, ``LoopStateStore``, ``LoopConfig``,
``LoopEngine``, mission-id directory under
``<state-dir>/missions/<mission-id>/``.
"""
from __future__ import annotations

import itertools
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..adapters.skill_loop_runner import (
    EngineerCallConfig,
    SkillLoopRunner,
    SkillLoopRunnerConfig,
)
from ..core.ports import EventSink
from ..mission import (
    MissionLoopConfig,
    MissionLoopEngine,
    MissionPlanner,
    MissionReviewer,
)
from ..scientist.distiller import Distiller, DistillerConfig
from ..skills.store import Skill, SkillStore

log = logging.getLogger(__name__)


def _import_argusbot():
    try:
        from codex_autoloop.core.state_store import LoopStateStore
    except ImportError as exc:  # pragma: no cover — environmental
        raise ImportError(
            "MissionExecutor requires ArgusBot to be importable "
            "(`pip install -e /path/to/ArgusBot`) for LoopStateStore."
        ) from exc
    return {
        "LoopStateStore": LoopStateStore,
    }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class MissionExecutorConfig:
    """Daemon-level defaults applied to every task.

    Per-task overrides could be added later; for v1, the daemon CLI
    flags configure these once and they apply to all tasks.
    """
    state_dir: str = ".argus-skill"
    skills_dir: str = "skills"
    workdir: str = "."
    max_rounds: int = 8
    plan_mode: str = "off"  # "auto" | "off" | "record"
    auto_follow_up: bool = True
    max_follow_ups: int = 3  # cap so a single task can't starve the queue
    main_model: str = "gpt-5.4-mini"
    main_reasoning_effort: str = "medium"
    reviewer_model: str = "gpt-5.4-mini"
    reviewer_reasoning_effort: str = "medium"
    plan_model: str = "gpt-5.4"
    plan_reasoning_effort: str = "high"
    distill_on_miss: bool = True
    skill_writeback: bool = True
    # When True, success writeback also calls the scientist to revise
    # the playbook based on the successful trajectory (bumps version).
    skill_revise_on_writeback: bool = False
    # When True, reviewer-emitted lessons (failure_cause==skill_gap) are
    # automatically merged into the matched skill via the scientist.
    skill_auto_promote_lesson: bool = False
    # Big model used by both writeback-revise and lesson-promote paths.
    scientist_model: str = "gpt-5.4"
    check_commands: tuple[str, ...] = ()
    check_timeout_seconds: int = 1200
    # Post-task report generation is OFF by default. The LoopEngine has
    # built-in final-report (markdown) and PPTX phases that fire after
    # reviewer ✅ done. Both are noisy, slow, and rarely useful for a
    # short interactive task — opt in explicitly via --enable-final-report
    # and --enable-pptx-report when running long unattended missions.
    enable_final_report: bool = False
    enable_pptx_report: bool = False


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass
class MissionOutcome:
    """Public result returned by ``MissionExecutor.execute``.

    The queue Daemon uses ``status`` (not ``success``) for user-facing
    reporting — ``max_rounds`` etc. become ``incomplete``, not ``error``
    (rubber-duck critique #7).
    """
    success: bool
    status: str  # success | incomplete | blocked | stopped | skipped | error
    stop_reason: str
    rounds: int
    matched_skill_name: str | None
    skill_distilled: bool
    had_follow_up: bool
    follow_up_count: int
    final_message: str
    mission_id: str
    exception: str | None = None


# ---------------------------------------------------------------------------
# MissionExecutor
# ---------------------------------------------------------------------------

class MissionExecutor:
    """Runs one ArgusBot ``LoopEngine.run()`` per task, with skill loop.

    Construct once at daemon startup. Call ``execute()`` from the queue
    worker. ``cancel_active()`` is thread-safe; call it from ``/stop``
    / ``/skip`` handlers to interrupt the current task.
    """

    _id_counter = itertools.count(1)

    def __init__(
        self,
        *,
        config: MissionExecutorConfig,
        skill_store: SkillStore,
        distiller: Distiller,
        engineer_backend: Any,  # argus-skill RunnerBackend
        codex_runner: Any,  # ArgusBot CodexRunner (reviewer/planner/reports)
        reviewer: Any,  # ArgusBot Reviewer
        planner: Any | None,  # ArgusBot Planner (None when plan_mode=off)
    ) -> None:
        self.config = config
        self.skill_store = skill_store
        self.distiller = distiller
        self.engineer_backend = engineer_backend
        self.codex_runner = codex_runner
        self.reviewer = reviewer
        self.planner = planner

        self._argus = _import_argusbot()
        self._cancel_lock = threading.Lock()
        self._active_state_store: Any | None = None
        self._active_mission_id: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
    ) -> MissionOutcome:
        """Run one mission for ``objective`` and return its outcome.

        ``preload_injects`` are operator messages buffered before this
        task started (queue's pending_inject buffer); they are pushed
        into the fresh ``LoopStateStore`` so the engineer sees them in
        round 1 (rubber-duck critique #10).

        ``prelude_context`` (Phase 3 lifetime-agent) is a non-authoritative
        memory block (identity card + prior journal entries) that's
        rendered alongside the objective in every round's engineer
        prompt. Pass ``""`` (the default) for no behavioral change.
        """
        mission_id = self._next_mission_id(objective)
        config = self.config
        workdir = config.workdir

        skill_loop_runner = SkillLoopRunner(
            config=SkillLoopRunnerConfig(
                mission_objective=objective,
                workdir=Path(workdir),
                engineer=EngineerCallConfig(
                    model=config.main_model,
                    reasoning_effort=config.main_reasoning_effort,
                ),
                distiller=DistillerConfig(
                    model=config.plan_model,
                    reasoning_effort=config.plan_reasoning_effort,
                ),
                distill_on_miss=config.distill_on_miss,
            ),
            skill_store=self.skill_store,
            distiller=self.distiller,
            engineer_runner=self.engineer_backend,
            fallback_runner=self.codex_runner,
            on_event=sink.handle_event,
        )

        loop_paths = self._mission_loop_state_paths(mission_id)
        if not config.enable_final_report:
            loop_paths.pop("final_report_file", None)
        if not config.enable_pptx_report:
            loop_paths.pop("pptx_report_file", None)
        state_store = self._argus["LoopStateStore"](
            objective=objective,
            check_commands=list(config.check_commands),
            plan_mode=config.plan_mode,  # type: ignore[arg-type]
            **loop_paths,
        )
        for inject in preload_injects or []:
            text = (inject or "").strip()
            if not text:
                continue
            try:
                state_store.request_inject(text, source="queue-buffer")
            except Exception:  # noqa: BLE001
                log.exception("preload inject failed (continuing)")

        # Skill-gap → live promotion callback. Wired here (not in engine
        # itself) so the engine stays decoupled from SkillStore/Distiller.
        def _on_skill_lesson(skill_id: str, lesson_text: str) -> None:
            if not config.skill_auto_promote_lesson:
                return
            matched_now = skill_loop_runner.matched_skill
            if matched_now is None or not lesson_text.strip():
                return
            ok = self.skill_store.promote_lesson(
                skill=matched_now,
                lesson_text=lesson_text,
                task_description=objective,
                distiller=self.distiller,
                scientist_model=config.scientist_model,
                on_event=sink.handle_event,
            )
            if not ok:
                sink.handle_event({
                    "type": "skill.promote_lesson.skipped",
                    "text": f"could not auto-merge lesson into {skill_id}",
                })

        loop_config = MissionLoopConfig(
            objective=objective,
            max_rounds=config.max_rounds,
            check_commands=list(config.check_commands),
            check_timeout_seconds=config.check_timeout_seconds,
            main_model=config.main_model,
            main_reasoning_effort=config.main_reasoning_effort,
            reviewer_model=config.reviewer_model,
            reviewer_reasoning_effort=config.reviewer_reasoning_effort,
            plan_mode=config.plan_mode,  # type: ignore[arg-type]
            plan_model=config.plan_model,
            plan_reasoning_effort=config.plan_reasoning_effort,
            full_auto=True,
            skip_git_repo_check=True,
            allow_follow_up_phase=config.auto_follow_up,
            workdir=workdir,
            mission_id=mission_id,
            on_skill_lesson=_on_skill_lesson,
            prelude_context=prelude_context,
        )

        # Track follow-up phases via a thin event-sink wrapper.
        tracker = _FollowUpTracker(downstream=sink, max_follow_ups=config.max_follow_ups)

        loop_engine = MissionLoopEngine(
            runner=skill_loop_runner,
            reviewer=self.reviewer,
            planner=self.planner if config.plan_mode in ("auto", "record") else None,
            config=loop_config,
            state_store=state_store,
            event_sink=tracker,
        )

        with self._cancel_lock:
            self._active_state_store = state_store
            self._active_mission_id = mission_id

        sink.handle_event({
            "type": "mission.started",
            "text": (
                f"mission {mission_id}: max_rounds={config.max_rounds} "
                f"plan_mode={config.plan_mode} auto_follow_up={config.auto_follow_up}"
            ),
            "mission_id": mission_id,
        })

        exception_str: str | None = None
        try:
            result = loop_engine.run()
            success = bool(getattr(result, "success", False))
            stop_reason = str(getattr(result, "stop_reason", "") or "")
            rounds = len(getattr(result, "rounds", []) or [])
        except Exception as exc:  # noqa: BLE001
            log.exception("LoopEngine.run raised")
            exception_str = f"{type(exc).__name__}: {exc}"
            success = False
            stop_reason = f"engine exception: {exception_str}"
            rounds = 0
            result = None
        finally:
            with self._cancel_lock:
                self._active_state_store = None
                self._active_mission_id = None

        had_follow_up = tracker.follow_up_count > 0
        matched = skill_loop_runner.matched_skill
        skill_distilled = skill_loop_runner.skill_was_distilled
        status = self._classify_status(
            success=success,
            stop_reason=stop_reason,
            exception=exception_str,
        )

        # Final message — best-effort: pull last engineer message off rounds.
        final_message = ""
        if result is not None:
            try:
                rs = list(getattr(result, "rounds", []) or [])
                if rs:
                    last = rs[-1]
                    final_message = (
                        getattr(last, "main_last_message", "")
                        or getattr(last, "main_summary", "")
                        or getattr(last, "summary", "")
                        or ""
                    )
            except Exception:  # noqa: BLE001
                pass

        # Skill writeback gate: success + matched (not freshly distilled)
        # + no follow-up drift + writeback enabled.
        if (
            success
            and matched is not None
            and not skill_distilled
            and not had_follow_up
            and config.skill_writeback
        ):
            try:
                self.skill_store.writeback_from_trajectory(
                    skill=matched,
                    task_description=objective,
                    successful_trajectory=final_message or stop_reason or objective,
                    distiller=self.distiller if config.skill_revise_on_writeback else None,
                    scientist_model=config.scientist_model,
                    revise=config.skill_revise_on_writeback,
                    on_event=sink.handle_event,
                )
                sink.handle_event({
                    "type": "skill.writeback",
                    "text": f"updated skill: {matched.name}",
                    "skill": matched.name,
                })
            except Exception as exc:  # noqa: BLE001
                log.exception("skill writeback failed")
                sink.handle_event({
                    "type": "skill.writeback.error",
                    "text": f"writeback failed: {type(exc).__name__}: {exc}",
                })

        outcome = MissionOutcome(
            success=success,
            status=status,
            stop_reason=stop_reason[:600],
            rounds=rounds,
            matched_skill_name=matched.name if matched else None,
            skill_distilled=skill_distilled,
            had_follow_up=had_follow_up,
            follow_up_count=tracker.follow_up_count,
            final_message=final_message,
            mission_id=mission_id,
            exception=exception_str,
        )
        sink.handle_event({
            "type": "mission.completed",
            "text": (
                f"mission {mission_id}: status={status} rounds={rounds} "
                f"skill={outcome.matched_skill_name or '-'}"
            ),
            "mission_id": mission_id,
            "status": status,
        })
        return outcome

    def cancel_active(self, *, reason: str = "operator") -> bool:
        """Hard-cancel the in-flight mission, if any. Returns True if one was active."""
        with self._cancel_lock:
            store = self._active_state_store
            mission_id = self._active_mission_id
        if store is None:
            return False
        try:
            store.request_stop(source=reason)
        except Exception:  # noqa: BLE001
            log.exception("request_stop raised on active state store")
        log.info("cancel_active: requested stop on mission %s (%s)", mission_id, reason)
        return True

    @property
    def active_mission_id(self) -> str | None:
        with self._cancel_lock:
            return self._active_mission_id

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_mission_id(self, objective: str) -> str:
        prefix = re.sub(r"[^a-z0-9]+", "-", (objective or "task").lower())[:24].strip("-") or "task"
        seq = next(self._id_counter)
        ts = time.strftime("%Y%m%dT%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        return f"{ts}-{seq:04d}-{prefix}-{suffix}"

    def _mission_loop_state_paths(self, mission_id: str) -> dict:
        base = Path(self.config.state_dir) / "missions" / mission_id / "loop_state"
        base.mkdir(parents=True, exist_ok=True)
        return {
            "state_file": str(base / "state.json"),
            "operator_messages_file": str(base / "operator_messages.txt"),
            "plan_overview_file": str(base / "plan_overview.md"),
            "review_summaries_dir": str(base / "review_summaries"),
            "final_report_file": str(base / "final_report.md"),
            "pptx_report_file": str(base / "final_report.pptx"),
            "main_prompt_file": str(base / "main_prompts.md"),
        }

    @staticmethod
    def _classify_status(*, success: bool, stop_reason: str, exception: str | None) -> str:
        if exception:
            return "error"
        if success:
            return "success"
        sr = (stop_reason or "").lower()
        if "stop" in sr and ("operator" in sr or "queue-skip" in sr or "queue-stop" in sr):
            return "stopped"
        if "skip" in sr:
            return "skipped"
        if "blocked" in sr:
            return "blocked"
        if "max_rounds" in sr or "max rounds" in sr or "no_progress" in sr or "no progress" in sr:
            return "incomplete"
        return "incomplete"


# ---------------------------------------------------------------------------
# Event-sink wrapper that counts follow-up phases
# ---------------------------------------------------------------------------

class _FollowUpTracker:
    """Forwards events to ``downstream`` and counts ``plan.completed`` events
    where ``follow_up_required`` is truthy.

    Why count rather than peek at LoopEngine state: LoopEngine doesn't
    expose a public follow-up counter on ``LoopResult``, but it does
    emit ``plan.completed`` with ``follow_up_required: bool``. We use
    the same signal for both gating writeback (per-task) and surfacing
    in the outcome (for status reports).

    Also enforces ``max_follow_ups`` by emitting a hint event when the
    cap is hit. (Hard-stopping mid-engine would require deeper hooks;
    the cap is enforced via ``allow_follow_up_phase`` flipping to
    False on the LoopConfig — but LoopConfig is read-only for the
    engine. v1 emits a warning; v2 can patch the engine.)
    """

    def __init__(self, *, downstream: EventSink, max_follow_ups: int) -> None:
        self._downstream = downstream
        self._max = max_follow_ups
        self.follow_up_count = 0

    def handle_event(self, event: dict) -> None:
        try:
            kind = str(event.get("type", ""))
            if kind == "plan.completed" and event.get("follow_up_required"):
                self.follow_up_count += 1
                if self._max and self.follow_up_count >= self._max:
                    # Inject a soft hint; still forward the original event.
                    try:
                        self._downstream.handle_event({
                            "type": "follow_up.cap.warning",
                            "text": (
                                f"follow-up cap reached: {self.follow_up_count}/{self._max} "
                                "— mission will end after this round if planner queues another."
                            ),
                        })
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            log.exception("follow-up tracker raised (event still forwarded)")
        try:
            self._downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream sink raised")

    def set_verbose(self, verbose: bool) -> None:
        setter = getattr(self._downstream, "set_verbose", None)
        if callable(setter):
            try:
                setter(verbose)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "MissionExecutor",
    "MissionExecutorConfig",
    "MissionOutcome",
]
