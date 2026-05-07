"""MissionLoopEngine — argus-skill's own LoopEngine.

Drop-in replacement for ``codex_autoloop.core.engine.LoopEngine`` on the
mission daemon path. Same public surface (``__init__`` signature plus a
``run() -> MissionLoopResult``) and same round-by-round event stream
(``round.started``, ``round.main.completed``, ``round.checks.completed``,
``round.review.completed``, ``round.control.injected``) so the chat-app
concise renderer and ``MissionDaemon`` keep working unchanged.

What's different from upstream:

- The main-agent prompts are NEUTRAL (see ``argus_skill.mission.prompts``).
  No "must do at least one concrete repo action" mandate, no
  DONE/REMAINING/BLOCKERS template, no task pre-classification.
- The reviewer is our ``MissionReviewer`` (also general-purpose; doesn't
  punish prose-only replies).
- The planner is our ``MissionPlanner`` (light JSON, follow-up only when
  obvious).
- We dropped upstream's stall-subagent + quota-exhaustion + no-progress
  watchdog. The mission daemon already has stop / inject / max_rounds; the
  extra heuristics added prompt complexity without buying much for an
  interactive agent. They can be reintroduced if needed.
- ``LoopStateStore`` is reused as-is: it's pure persistence + control bus.
- ``RoundSummary`` / ``PlanDecision`` are reused from upstream models so
  ``state_store.record_round`` keeps its current type hints.

Concurrency / control:

- ``state_store.is_stop_requested()`` is checked at the top of every
  round and after each main-agent call.
- ``state_store.consume_interrupt_reason`` is wired via the SkillLoopRunner
  options; an in-flight engineer call can be interrupted by ``/stop`` or
  ``/inject``.
- ``state_store.consume_pending_instruction()`` returns any operator
  ``/inject`` text after an interrupt; we then build an
  ``operator_override_prompt`` for the next round.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.models import CheckResult, ReviewDecision
from ..engineer.checks import all_checks_passed, run_checks
from ..skills.lessons import default_pending_lessons_dir, record_pending_lesson
from . import prompts as mission_prompts
from .reviewer import MissionReviewer, MissionReviewerConfig
from .planner import MissionPlanner, MissionPlannerConfig

log = logging.getLogger(__name__)


def _import_upstream_models():
    try:
        from codex_autoloop.models import PlanDecision, RoundSummary  # type: ignore
    except ImportError as exc:  # pragma: no cover — environmental
        raise ImportError(
            "MissionLoopEngine requires codex_autoloop.models to be importable."
        ) from exc
    return RoundSummary, PlanDecision


@dataclass
class MissionLoopConfig:
    """Knobs for one mission run.

    Mirrors the subset of ``codex_autoloop.core.engine.LoopConfig`` that
    argus-skill's mission daemon actually populates. Fields we don't use
    (stall watchdog, add_dirs, plugin_dirs, worktree_name, quota detection,
    no-progress watchdog) are omitted on purpose.
    """
    objective: str
    max_rounds: int = 50
    check_commands: list[str] = field(default_factory=list)
    check_timeout_seconds: int = 1200
    main_model: str | None = None
    main_reasoning_effort: str | None = None
    main_extra_args: list[str] | None = None
    reviewer_model: str | None = None
    reviewer_reasoning_effort: str | None = None
    reviewer_extra_args: list[str] | None = None
    plan_model: str | None = None
    plan_reasoning_effort: str | None = None
    plan_extra_args: list[str] | None = None
    plan_mode: str = "off"  # "off" | "auto" | "record"
    skip_git_repo_check: bool = True
    full_auto: bool = True
    dangerous_yolo: bool = False
    initial_session_id: str | None = None
    allow_follow_up_phase: bool = False
    workdir: str | None = None
    # Phase 1 reviewer→skill feedback loop knobs.
    # When the reviewer emits a mission_lesson with failure_cause==skill_gap
    # we record it under ``pending_lessons_dir/<skill_slug>/`` for offline
    # promotion. ``None`` ⇒ default location next to skills/.
    pending_lessons_dir: str | None = None
    mission_id: str | None = None
    # Phase 2 reviewer→skill feedback loop: when set, engine invokes
    # this callback IMMEDIATELY after recording a pending lesson. It
    # receives ``(skill_id, lesson_text)`` and is expected to merge the
    # lesson into the live skill markdown (e.g. via
    # ``SkillStore.promote_lesson``). Best-effort: exceptions are
    # caught and logged, the engine never crashes on them.
    on_skill_lesson: Callable[[str, str], None] | None = None
    # Phase 3 lifetime-agent: a non-authoritative memory block (identity
    # card + relevant prior journal entries) rendered alongside the
    # objective. Empty string ⇒ no preamble, no behavioral change. The
    # block's own header marks it advisory, but we additionally keep it
    # *outside* the ``Objective`` section so skill-matching / mission-id
    # hashing / reviewer prompts that read ``cfg.objective`` are
    # unaffected.
    prelude_context: str = ""


@dataclass
class MissionLoopResult:
    success: bool
    session_id: str | None
    rounds: list  # list[RoundSummary] (upstream model)
    stop_reason: str


class MissionLoopEngine:
    """Round-loop state machine for one mission.

    Args:
        runner: Object exposing ``.run_exec(prompt, resume_thread_id,
            options, run_label) -> CodexRunResult-shaped result``. In
            practice this is ``SkillLoopRunner`` from
            ``argus_skill.adapters.skill_loop_runner``, which does the
            matcher + distill + engineer dance.
        reviewer: ``MissionReviewer``-like (must expose ``evaluate(...)``).
        planner: ``MissionPlanner`` or None (None ⇒ plan_mode is "off"
            from the engine's perspective regardless of config).
        config: ``MissionLoopConfig``.
        state_store: upstream ``LoopStateStore`` (reused for persistence
            and control-bus semantics).
        event_sink: dict-emitting sink callable. We use the upstream's
            ``EventSink`` Protocol (``handle_event(dict)``) when given
            an object, or accept a bare callable for tests.
    """

    def __init__(
        self,
        *,
        runner: Any,
        reviewer: MissionReviewer,
        planner: MissionPlanner | None,
        config: MissionLoopConfig,
        state_store: Any,
        event_sink: Any = None,
    ) -> None:
        self.runner = runner
        self.reviewer = reviewer
        self.planner = planner
        self.config = config
        self.state_store = state_store
        self.event_sink = event_sink
        self._RoundSummary, self._PlanDecision = _import_upstream_models()
        # Phase 1 reviewer→skill loop: transient mission-local "skill
        # patch" carried into the next round's prompt. Cleared on every
        # successful round and on every clean reviewer verdict that
        # didn't classify the failure as ``skill_gap``.
        self._active_lesson: str = ""
        self._active_verification_evidence: dict | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> MissionLoopResult:
        rounds: list = []
        session_id = self.config.initial_session_id
        current_plan: Any = None  # PlanDecision
        in_follow_up_phase = False

        self._emit({
            "type": "loop.started",
            "objective": self.config.objective,
            "max_rounds": self.config.max_rounds,
            "session_id": session_id,
            "plan_mode": self._current_plan_mode(),
        })

        next_prompt = mission_prompts.initial_main_prompt(
            objective=self.config.objective,
            operator_messages=self._operator_messages_for("main"),
            plan=current_plan,
            prelude_context=self.config.prelude_context,
        )
        next_phase = "initial"

        for round_index in range(1, self.config.max_rounds + 1):
            if self.state_store.is_stop_requested():
                return self._complete(
                    success=False, session_id=session_id, rounds=rounds,
                    stop_reason="Stopped by operator command.",
                )

            self._emit({
                "type": "round.started",
                "round_index": round_index,
                "session_id": session_id,
            })
            self._record_main_prompt(round_index=round_index, phase=next_phase, prompt=next_prompt)

            main_result = self._call_main(prompt=next_prompt, session_id=session_id)
            session_id = getattr(main_result, "thread_id", None) or session_id
            self._emit({
                "type": "round.main.completed",
                "round_index": round_index,
                "session_id": session_id,
                "exit_code": getattr(main_result, "exit_code", 0),
                "turn_completed": getattr(main_result, "turn_completed", True),
                "turn_failed": getattr(main_result, "turn_failed", False),
                "fatal_error": getattr(main_result, "fatal_error", None),
                "last_message": getattr(main_result, "last_agent_message", "") or "",
                "input_tokens": int(getattr(main_result, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(main_result, "output_tokens", 0) or 0),
            })

            # --- handle external interruption --------------------------------
            fatal = getattr(main_result, "fatal_error", None)
            if isinstance(fatal, str) and fatal.startswith("External interrupt:"):
                review = self._build_interrupt_review(main_result)
                self._emit_review(round_index, review)

                round_summary = self._RoundSummary(
                    round_index=round_index,
                    thread_id=session_id,
                    main_exit_code=getattr(main_result, "exit_code", -1),
                    main_turn_completed=getattr(main_result, "turn_completed", False),
                    main_turn_failed=True,
                    checks=[],
                    review=review,
                    main_last_message=getattr(main_result, "last_agent_message", "") or "",
                    plan=current_plan,
                )
                rounds.append(round_summary)
                self.state_store.record_round(
                    round_summary,
                    session_id=session_id,
                    current_review=review,
                    current_plan=current_plan,
                )

                if self.state_store.is_stop_requested():
                    return self._complete(
                        success=False, session_id=session_id, rounds=rounds,
                        stop_reason="Stopped by operator command.",
                    )

                injected = self.state_store.consume_pending_instruction()
                if injected:
                    self._emit({
                        "type": "round.control.injected",
                        "round_index": round_index,
                        "instruction": injected,
                    })
                    next_prompt = mission_prompts.operator_override_prompt(
                        objective=self.config.objective,
                        instruction=injected,
                        operator_messages=self._operator_messages_for("main"),
                        plan=current_plan,
                    )
                    next_phase = "operator-override"
                else:
                    next_prompt = mission_prompts.continue_main_prompt(
                        objective=self.config.objective,
                        review=review,
                        checks_ok=False,
                        operator_messages=self._operator_messages_for("main"),
                        plan=current_plan,
                        prelude_context=self.config.prelude_context,
                    )
                    next_phase = "continue"
                continue

            # --- run acceptance checks --------------------------------------
            checks = run_checks(
                self.config.check_commands or [],
                self.config.check_timeout_seconds,
                cwd=self.config.workdir,
            )
            self._emit({
                "type": "round.checks.completed",
                "round_index": round_index,
                "checks": [
                    {
                        "command": c.command,
                        "exit_code": c.exit_code,
                        "passed": c.passed,
                    }
                    for c in checks
                ],
            })
            checks_ok = all_checks_passed(checks) if checks else True

            # --- assemble verification context for the reviewer ------------
            verification_context = self._build_verification_context(
                checks=checks,
                main_result=main_result,
                checks_ok=checks_ok,
            )
            active_skill = self._current_skill_id()

            # --- ask the reviewer -------------------------------------------
            review = self.reviewer.evaluate(
                objective=self.config.objective,
                round_index=round_index,
                session_id=session_id,
                main_summary=getattr(main_result, "last_agent_message", "") or "",
                main_error=getattr(main_result, "fatal_error", None),
                checks=checks,
                config=MissionReviewerConfig(
                    model=self.config.reviewer_model,
                    reasoning_effort=self.config.reviewer_reasoning_effort,
                    extra_args=list(self.config.reviewer_extra_args or []),
                    skip_git_repo_check=self.config.skip_git_repo_check,
                    full_auto=self.config.full_auto,
                    dangerous_yolo=self.config.dangerous_yolo,
                ),
                operator_messages=self._operator_messages_for("review"),
                planner_review_instruction=(
                    getattr(current_plan, "review_instruction", "") if current_plan else ""
                ),
                verification_context=verification_context,
                active_skill_id=active_skill,
            )
            self._emit_review(round_index, review)

            # --- update mission-local skill-patch overlay -------------------
            self._update_active_lesson(
                review=review,
                checks_ok=checks_ok,
                round_index=round_index,
                verification_context=verification_context,
                active_skill_id=active_skill,
            )

            # --- maybe ask the planner --------------------------------------
            planned_follow_up: Any = None
            current_plan_mode = self._current_plan_mode()
            if (
                review.status == "done"
                and checks_ok
                and current_plan_mode != "off"
                and self.planner is not None
                and not in_follow_up_phase
            ):
                planned_follow_up = self._call_planner(
                    round_index=round_index,
                    review=review,
                    main_summary=getattr(main_result, "last_agent_message", "") or "",
                )

            round_summary = self._RoundSummary(
                round_index=round_index,
                thread_id=session_id,
                main_exit_code=getattr(main_result, "exit_code", 0),
                main_turn_completed=getattr(main_result, "turn_completed", True),
                main_turn_failed=getattr(main_result, "turn_failed", False),
                checks=checks,
                review=review,
                main_last_message=getattr(main_result, "last_agent_message", "") or "",
                plan=planned_follow_up if planned_follow_up is not None else current_plan,
            )
            rounds.append(round_summary)
            self.state_store.record_round(
                round_summary,
                session_id=session_id,
                current_review=review,
                current_plan=planned_follow_up if planned_follow_up is not None else current_plan,
            )

            # --- terminal verdicts ------------------------------------------
            if review.status == "done" and checks_ok:
                if current_plan_mode == "off":
                    return self._complete(
                        success=True, session_id=session_id, rounds=rounds,
                        stop_reason="Reviewer marked done and acceptance checks passed.",
                    )
                if current_plan_mode == "record":
                    return self._complete(
                        success=True, session_id=session_id, rounds=rounds,
                        stop_reason="Reviewer marked done; planner recorded the final summary.",
                    )
                # plan_mode == "auto"
                if not self.config.allow_follow_up_phase:
                    return self._complete(
                        success=True, session_id=session_id, rounds=rounds,
                        stop_reason="Reviewer marked done; follow-up phase is disabled.",
                    )
                if planned_follow_up is None or not getattr(planned_follow_up, "follow_up_required", False):
                    return self._complete(
                        success=True, session_id=session_id, rounds=rounds,
                        stop_reason="Reviewer marked done; planner did not request a follow-up.",
                    )
                # Enter the follow-up phase: one more round with the planner-supplied sub-objective.
                current_plan = planned_follow_up
                in_follow_up_phase = True
                next_prompt = mission_prompts.follow_up_prompt(
                    objective=self.config.objective,
                    plan=current_plan,
                    operator_messages=self._operator_messages_for("main"),
                )
                next_phase = "follow-up"
                continue

            if review.status == "blocked":
                return self._complete(
                    success=False, session_id=session_id, rounds=rounds,
                    stop_reason=f"Reviewer blocked: {review.reason}",
                )

            # status == "continue"
            if in_follow_up_phase:
                # We don't iterate the follow-up — one shot only.
                return self._complete(
                    success=True, session_id=session_id, rounds=rounds,
                    stop_reason="Follow-up round did not converge to done; mission ends.",
                )

            next_prompt = mission_prompts.continue_main_prompt(
                objective=self.config.objective,
                review=review,
                checks_ok=checks_ok,
                operator_messages=self._operator_messages_for("main"),
                plan=current_plan,
                mission_lesson=self._active_lesson,
                verification_evidence=self._active_verification_evidence,
                prelude_context=self.config.prelude_context,
            )
            next_phase = "continue"

        return self._complete(
            success=False, session_id=session_id, rounds=rounds,
            stop_reason=f"Reached max rounds ({self.config.max_rounds}).",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call_main(self, *, prompt: str, session_id: str | None) -> Any:
        """Invoke the main-agent runner.

        We piggyback on upstream's ``RunnerOptions`` because that's what
        ``SkillLoopRunner`` (and any future ``CodexRunner``-style backend)
        expects on its ``run_exec`` call. The runner's option translator
        then maps watchdog / interrupt callbacks into the right backend.
        """
        try:
            from codex_autoloop.codex_runner import RunnerOptions as UpstreamRunnerOptions  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "MissionLoopEngine requires codex_autoloop.codex_runner.RunnerOptions."
            ) from exc

        options = UpstreamRunnerOptions(
            model=self.config.main_model,
            reasoning_effort=self.config.main_reasoning_effort,
            dangerous_yolo=self.config.dangerous_yolo,
            full_auto=self.config.full_auto,
            skip_git_repo_check=self.config.skip_git_repo_check,
            extra_args=self.config.main_extra_args,
            external_interrupt_reason_provider=self.state_store.consume_interrupt_reason,
        )
        return self.runner.run_exec(
            prompt=prompt,
            resume_thread_id=session_id,
            options=options,
            run_label="main",
        )

    def _call_planner(
        self,
        *,
        round_index: int,
        review: ReviewDecision,
        main_summary: str,
    ) -> Any:
        if self.planner is None:
            return None
        try:
            return self.planner.evaluate(
                objective=self.config.objective,
                round_index=round_index,
                latest_review_summary=review.completion_summary_markdown
                or review.round_summary_markdown,
                latest_main_summary=main_summary,
                config=MissionPlannerConfig(
                    model=self.config.plan_model,
                    reasoning_effort=self.config.plan_reasoning_effort,
                    extra_args=list(self.config.plan_extra_args or []),
                    skip_git_repo_check=self.config.skip_git_repo_check,
                    full_auto=self.config.full_auto,
                    dangerous_yolo=self.config.dangerous_yolo,
                ),
                operator_messages=self._operator_messages_for("plan"),
                plan_direction=self._consume_plan_direction(),
            )
        except Exception as exc:  # noqa: BLE001 — planner failure shouldn't abort mission
            log.warning("planner.evaluate raised: %s", exc)
            return None

    def _build_interrupt_review(self, main_result: Any) -> ReviewDecision:
        fatal = getattr(main_result, "fatal_error", "External interrupt: unknown")
        last = getattr(main_result, "last_agent_message", "") or "none"
        return ReviewDecision(
            status="continue",
            confidence=1.0,
            reason=fatal,
            next_action="Apply operator interruption (or continue prior objective).",
            round_summary_markdown=(
                "# Review Summary\n\n"
                f"- Round interrupted: {fatal}\n"
                f"- Latest main summary: {last}\n"
            ),
            completion_summary_markdown="",
        )

    def _emit_review(self, round_index: int, review: ReviewDecision) -> None:
        self._emit({
            "type": "round.review.completed",
            "round_index": round_index,
            "status": review.status,
            "confidence": review.confidence,
            "reason": review.reason,
            "next_action": review.next_action,
            "failure_cause": getattr(review, "failure_cause", "") or "",
            "mission_lesson_emitted": bool((getattr(review, "mission_lesson", "") or "").strip()),
            "input_tokens": int(getattr(review, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(review, "output_tokens", 0) or 0),
        })

    # ------------------------------------------------------------------
    # Phase 1 reviewer→skill loop helpers
    # ------------------------------------------------------------------

    def _build_verification_context(
        self,
        *,
        checks: list[CheckResult],
        main_result: Any,
        checks_ok: bool,
    ) -> dict:
        """Pack raw check evidence so the reviewer can classify the failure
        from facts, not from the engineer's prose.

        Always returns a dict (possibly empty); reviewer prompt handles
        the empty case explicitly. Engineer last message is included
        verbatim so the reviewer can spot self-reported "all green"
        claims that don't match the raw command output.
        """
        ctx: dict[str, Any] = {}
        if not checks_ok:
            failed = [c for c in checks if not c.passed]
            if failed:
                # First failed check is usually the most informative.
                first = failed[0]
                ctx["cmd"] = first.command
                ctx["exit_code"] = first.exit_code
                # ``CheckResult.output_tail`` already merges stdout/stderr;
                # we surface it as stderr_tail so the reviewer treats it
                # as the failure transcript.
                ctx["stderr_tail"] = first.output_tail or ""
                if len(failed) > 1:
                    ctx["notes"] = (
                        f"{len(failed)} of {len(checks)} acceptance checks failed; "
                        "showing the first failing one."
                    )
        engineer_last = getattr(main_result, "last_agent_message", "") or ""
        if engineer_last:
            ctx["engineer_last_msg"] = engineer_last
        # Independent runtime evidence captured by the runner after the
        # engineer round (e.g. ContainerCodexRunner.state_probe_cmd):
        # listening ports, files written, recent processes. Surfaces
        # truth to the reviewer when the engineer self-report is
        # inaccurate (service died, output value wrong, etc).
        runtime_probe = getattr(main_result, "runtime_probe", "") or ""
        if runtime_probe:
            ctx["runtime_probe"] = runtime_probe
        # Phase 4: surface official verifier (TB v2 /tests/test.sh)
        # signal so the reviewer can see ground-truth pass/fail. The
        # runner attaches these via setattr when verify_cmd ran.
        verify_exit = getattr(main_result, "verify_exit", None)
        if verify_exit is not None:
            ctx["verify_exit"] = int(verify_exit)
            ctx["verify_cmd"] = getattr(main_result, "verify_cmd", "") or ""
            v_out = getattr(main_result, "verify_stdout_tail", "") or ""
            v_err = getattr(main_result, "verify_stderr_tail", "") or ""
            if v_out:
                ctx["verify_stdout_tail"] = v_out
            if v_err:
                ctx["verify_stderr_tail"] = v_err
        return ctx

    def _current_skill_id(self) -> str | None:
        """Best-effort skill identifier for reviewer + lesson-store wiring."""
        skill = getattr(self.runner, "matched_skill", None)
        if skill is None:
            return None
        return getattr(skill, "name", None) or getattr(skill, "path", None) or None

    def _update_active_lesson(
        self,
        *,
        review: ReviewDecision,
        checks_ok: bool,
        round_index: int,
        verification_context: dict,
        active_skill_id: str | None,
    ) -> None:
        """Maintain the mission-local skill-patch overlay.

        - Round succeeded (status==done && checks_ok) → clear overlay.
        - Reviewer classified failure_cause==skill_gap and emitted
          mission_lesson → adopt it as ``self._active_lesson`` and
          mirror the raw failure evidence so the next round's prompt
          can include it. Best-effort persist to pending_lessons/.
        - Any other case → leave previous overlay intact unless
          reviewer explicitly cleared it via failure_cause classification
          (we *don't* clear on non-skill_gap to avoid losing a still-
          valid lesson when the next round hits a different cause).
        """
        if review.status == "done" and checks_ok:
            self._active_lesson = ""
            self._active_verification_evidence = None
            return

        # Always forward the latest verification evidence to the engineer's
        # continue prompt — not just on the skill_gap branch. The engineer
        # otherwise gets nothing but the reviewer's prose, even when the
        # reviewer has rich ground-truth data (verifier output, still-
        # failing acceptance tests, runtime probes, etc.).
        self._active_verification_evidence = (
            {k: v for k, v in verification_context.items() if v not in (None, "", [])}
            if verification_context
            else None
        )

        lesson = (getattr(review, "mission_lesson", "") or "").strip()
        cause = (getattr(review, "failure_cause", "") or "").strip()
        if lesson and cause == "skill_gap":
            self._active_lesson = lesson
            self._record_pending_lesson(
                review=review,
                round_index=round_index,
                verification_context=verification_context,
                active_skill_id=active_skill_id,
            )
            cb = self.config.on_skill_lesson
            if cb is not None and active_skill_id:
                try:
                    cb(active_skill_id, lesson)
                except Exception as exc:  # noqa: BLE001 — best-effort
                    log.warning("on_skill_lesson callback failed: %s: %s",
                                type(exc).__name__, exc)
        # Else: keep prior lesson untouched. Engineer still gets
        # `next_action` feedback + verification evidence in the continue
        # prompt.

    def _record_pending_lesson(
        self,
        *,
        review: ReviewDecision,
        round_index: int,
        verification_context: dict,
        active_skill_id: str | None,
    ) -> None:
        try:
            if self.config.pending_lessons_dir:
                pending_dir = Path(self.config.pending_lessons_dir)
            else:
                skills_dir: Any = None
                store = getattr(self.runner, "skill_store", None)
                if store is not None:
                    skills_dir = getattr(store, "skills_dir", None)
                pending_dir = default_pending_lessons_dir(skills_dir)
        except Exception:
            return
        record_pending_lesson(
            pending_dir=pending_dir,
            skill_id=active_skill_id,
            mission_id=self.config.mission_id,
            objective=self.config.objective,
            decision=review,
            verification_context=verification_context,
            round_index=round_index,
        )

    def _complete(
        self,
        *,
        success: bool,
        session_id: str | None,
        rounds: list,
        stop_reason: str,
    ) -> MissionLoopResult:
        try:
            self.state_store.record_completion(
                success=success, stop_reason=stop_reason, session_id=session_id,
            )
        except Exception:  # noqa: BLE001 — never let persistence failure mask result
            log.exception("state_store.record_completion raised")
        self._emit({
            "type": "loop.completed",
            "success": success,
            "session_id": session_id,
            "stop_reason": stop_reason,
            "rounds": len(rounds),
        })
        return MissionLoopResult(
            success=success,
            session_id=session_id,
            rounds=rounds,
            stop_reason=stop_reason,
        )

    # ------------------------------------------------------------------
    # State-store helpers (degrade gracefully on stubs / minimal bus)
    # ------------------------------------------------------------------

    def _operator_messages_for(self, role: str) -> list[str]:
        fn = getattr(self.state_store, "list_messages_for_role", None)
        if fn is None:
            return []
        try:
            messages = fn(role) or []
        except Exception:  # noqa: BLE001
            log.debug("list_messages_for_role(%s) raised", role, exc_info=True)
            return []
        return [str(m) for m in messages]

    def _record_main_prompt(self, *, round_index: int, phase: str, prompt: str) -> None:
        fn = getattr(self.state_store, "record_main_prompt", None)
        if fn is None:
            return
        try:
            fn(round_index=round_index, phase=phase, prompt=prompt)
        except Exception:  # noqa: BLE001
            log.debug("record_main_prompt raised", exc_info=True)

    def _consume_plan_direction(self) -> str:
        fn = getattr(self.state_store, "consume_plan_direction", None)
        if fn is None:
            return ""
        try:
            return str(fn() or "")
        except Exception:  # noqa: BLE001
            return ""

    def _current_plan_mode(self) -> str:
        fn = getattr(self.state_store, "effective_plan_mode", None)
        if fn is None:
            return self.config.plan_mode
        try:
            return str(fn() or self.config.plan_mode)
        except Exception:  # noqa: BLE001
            return self.config.plan_mode

    def _emit(self, event: dict) -> None:
        sink = self.event_sink
        if sink is None:
            return
        try:
            handle = getattr(sink, "handle_event", None)
            if handle is not None:
                handle(event)
            elif callable(sink):
                sink(event)
        except Exception:  # noqa: BLE001 — never let UI errors kill the loop
            log.exception("event_sink raised")


__all__ = [
    "MissionLoopConfig",
    "MissionLoopEngine",
    "MissionLoopResult",
]
