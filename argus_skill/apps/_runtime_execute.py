"""Execute-lifecycle mixin: ``SkillLoopExecuteMixin`` — the
``_SkillLoopRunner.execute()`` orchestrator and its lifecycle-phase helper
methods (config build, mission-context prep, bounded planning, loop
invocation, outcome-field extraction, stage-transition decision, outcome
assembly).

Split out of ``_runtime.py`` so that module stays under the maintainability
line-count target. Every name here is re-exported from ``_runtime.py`` (see
its module docstring and ``__all__``) so external imports are unaffected.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ..core.knobs import resolve_role_reasoning_effort
from ..core.ports import EventSink
from ..core.research_direction import normalize_research_direction
from ..engineer.runner import should_clear_thread_id_after_outcome
from ._env import env_flag as _env_flag
from ._runtime_backends import _Outcome
from ._runtime_helpers import (
    _checkpoint_path_for,
    _ExecuteState,
    _project_state_dir_for,
    _should_run_stage_transition,
)

log = logging.getLogger(__name__)


class SkillLoopExecuteMixin:
    """Mission-execution half of ``_SkillLoopRunner``."""

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",
        sink: EventSink,
        preload_injects: list[str] | None = None,  # noqa: ARG002 — protocol parity
        prelude_context: str = "",
        seed_thread_id: str | None = None,
        scope: str = "",
        preplanned: bool = False,
        mission_id: str | None = None,
        usage_mission_id: str | None = None,
        context_packet_path: str = "",
        max_rounds_override: int | None = None,
        progressive_experiment_matrix: bool = False,
        workflow_mode_override: str = "",
        require_independent_review: bool = False,
        working_dir_override: str = "",
        maintenance_mission: bool = False,
    ) -> _Outcome:
        # Chat fast-path (operator-front-door-only; gated by _allow_chat_fast_path).
        # The classifier + reply logic lives in ``_maybe_chat_outcome``; here we
        # only gate it so the 7×24 daemon (``_allow_chat_fast_path=False``) does
        # not classify arbitrary autonomous work — agent-produced backlog work
        # must not be second-guessed.
        chat_outcome = self._execute_chat_fast_path(
            objective=objective,
            sink=sink,
            seed_thread_id=seed_thread_id,
            mission_id=mission_id,
            usage_mission_id=usage_mission_id,
        )
        if chat_outcome is not None:
            return chat_outcome

        ex_state = _ExecuteState()
        self._build_execute_config(
            ex_state,
            working_dir_override=working_dir_override,
            maintenance_mission=maintenance_mission,
            require_independent_review=require_independent_review,
            max_rounds_override=max_rounds_override,
            progressive_experiment_matrix=progressive_experiment_matrix,
            context_packet_path=context_packet_path,
            mission_id=mission_id,
            workflow_mode_override=workflow_mode_override,
        )
        self._build_execute_skill_store_and_loop(ex_state, sink=sink)
        self._prepare_execute_mission_context(
            ex_state,
            objective=objective,
            prelude_context=prelude_context,
            seed_thread_id=seed_thread_id,
            scope=scope,
        )
        self._invoke_execute_loop(
            ex_state,
            sink=sink,
            objective=objective,
            original_objective=original_objective,
            preplanned=preplanned,
            mission_id=mission_id,
            usage_mission_id=usage_mission_id,
        )
        self._extract_execute_outcome_fields(ex_state)
        self._maybe_decide_stage_transition(
            ex_state,
            sink=sink,
            mission_id=mission_id,
            usage_mission_id=usage_mission_id,
            maintenance_mission=maintenance_mission,
        )
        return self._build_execute_outcome(ex_state)

    def _execute_chat_fast_path(
        self,
        *,
        objective: str,
        sink: EventSink,
        seed_thread_id: str | None,
        mission_id: str | None,
        usage_mission_id: str | None,
    ) -> "_Outcome | None":
        """Classify and answer an operator-front-door chat message, if the
        classifier decides this objective is chat rather than mission work.
        Returns ``None`` when the caller should proceed with a real mission
        (the 7×24 daemon never reaches the classifier: it always gets ``None``).
        """
        if not self._allow_chat_fast_path:
            return None
        self._set_usage_context(usage_mission_id or mission_id)
        try:
            return self._maybe_chat_outcome(
                objective=objective,
                sink=sink,
                seed_thread_id=seed_thread_id,
            )
        finally:
            self._set_usage_context(None)

    def _build_execute_config(
        self,
        ex_state: "_ExecuteState",
        *,
        working_dir_override: str,
        maintenance_mission: bool,
        require_independent_review: bool,
        max_rounds_override: int | None,
        progressive_experiment_matrix: bool,
        context_packet_path: str,
        mission_id: str | None,
        workflow_mode_override: str,
    ) -> None:
        """Resolve the workdir/vertical-derived flags and build the
        ``SkillLoopConfig`` for this mission.
        """
        args = self._args
        # Lazy proxy: ``_independent_review_required_for_project_root``,
        # ``_workflow_mode_for_project_root``, and
        # ``_paper_mission_for_project_root`` (used below) live in
        # ``_runtime_supervisor`` but are re-exported on — and monkeypatched
        # directly against — the ``_runtime`` facade module by tests (e.g.
        # tests/life/test_chat_fast_path.py). Resolving them here at call
        # time keeps that monkeypatch effective even though this method
        # lives in a sibling module.
        from ._runtime import (
            _independent_review_required_for_project_root,
            _paper_mission_for_project_root,
            _workflow_mode_for_project_root,
        )

        workdir = (
            Path(working_dir_override).expanduser().resolve()
            if working_dir_override
            else Path(args.workdir).expanduser()
            if args.workdir
            else Path.cwd()
        )
        _proot = (
            workdir
            if maintenance_mission
            else Path(getattr(self, "_artifact_root", None) or workdir)
        )
        effective_require_independent_review = (
            require_independent_review or _independent_review_required_for_project_root(_proot)
        )
        # 7×24 product: default to dangerous_yolo (no bwrap sandbox).
        # The operator runs the daemon on their own box and explicitly
        # consents to autonomous execution; the sandbox only fights us
        # (`bwrap: Can't create file at /.codex: Permission denied`).
        # Operators can opt back into sandbox via ARGUS_SKILL_SAFE_MODE=1.
        # Framework-maintenance roles are always confined to their private
        # worktree and receive no push-capable VCS credentials. Authenticated
        # commit/push/PR publication remains daemon-owned after Reviewer approval.
        safe_mode = True if maintenance_mission else _env_flag("ARGUS_SKILL_SAFE_MODE", False)
        config_kwargs = {
            "engineer_model": args.engineer_model,
            "reviewer_model": args.reviewer_model,
            "engineer_initial_reasoning_effort": os.environ.get(
                "ARGUS_SKILL_ENGINEER_INITIAL_REASONING_EFFORT", "high"
            ),
            "engineer_reasoning_effort": getattr(args, "engineer_reasoning_effort", "xhigh"),
            "reviewer_reasoning_effort": getattr(
                args,
                "reviewer_reasoning_effort",
                "xhigh",
            ),
            "max_rounds": (
                max(1, int(max_rounds_override))
                if max_rounds_override is not None
                else args.max_rounds
            ),
            "skill_ops_enabled": _env_flag(
                "ARGUS_SKILL_SKILL_OPS",
                default=True,
            ),
            "wiki_ops_enabled": _env_flag(
                "ARGUS_SKILL_WIKI_OPS",
                default=True,
            ),
            "auto_init_wiki": _env_flag(
                "ARGUS_SKILL_AUTO_INIT_WIKI",
                default=True,
            ),
            "auto_compact_enabled": _env_flag(
                "ARGUS_SKILL_AUTO_COMPACT",
                # Compaction is an explicit maintenance operation, not part of
                # every mission close. Per-mission sweeps scale with the entire
                # shared library and historically regenerated/archived the same
                # duplicates in a costly loop.
                default=False,
            ),
            "dangerous_yolo": not safe_mode,
            "full_auto": safe_mode,
            "sandbox_mode": "workspace-write" if maintenance_mission else None,
            "isolate_workdir": maintenance_mission,
            "skip_git_repo_check": True,
            "engineer_self_review_enabled": (
                _env_flag("ARGUS_SKILL_ENGINEER_SELF_REVIEW", default=True)
                and not effective_require_independent_review
            ),
            # Filled from the resolved vertical below.  Fail-safe default: an
            # undecided task is bounded/non-paper.
            "paper_mission": False,
            # Shared Markdown checkpoint in internal project state. Engineer
            # and Reviewer receive its absolute path and edit it in sequence;
            # output workdirs contain deliverables only.
            "checkpoint_path": _checkpoint_path_for(
                args,
                Path(args.workdir).expanduser() if args.workdir else Path.cwd(),
            ),
            "context_packet_path": str(context_packet_path or ""),
            "session_id": mission_id,
            # Process-correctness audit: the reviewer runs in the project
            # work-tree and only sees the engineer's final summary. Give it the
            # ABSOLUTE path to this project's engineer execution log
            # (``<life_dir>/events.jsonl``) so it can grep HOW the result was
            # produced. This runtime log remains outside the worktree.
        }
        if progressive_experiment_matrix:
            # Matrix closure is governed by measurable progress/stall detection,
            # not an arbitrary round count. This value is practically unbounded
            # while preserving the existing integer config/event schema.
            config_kwargs["max_rounds"] = 2_147_483_647
            config_kwargs["soft_round_limit"] = 0
            config_kwargs["hard_escalate_rounds"] = 0
        maintenance_checkpoint_dir: Path | None = None
        if context_packet_path:
            config_kwargs["checkpoint_path"] = (
                Path(context_packet_path).expanduser().resolve().parent / "CHECKPOINT.md"
            )
        if maintenance_mission:
            maintenance_checkpoint_dir = workdir / ".argus-self-maintenance-runtime"
            maintenance_checkpoint_dir.mkdir(parents=True, exist_ok=True)
            config_kwargs["checkpoint_path"] = maintenance_checkpoint_dir / "CHECKPOINT.md"
        _project_state_dir = _project_state_dir_for(
            args, Path(args.workdir).expanduser() if args.workdir else Path.cwd()
        )
        config_kwargs["engineer_log_path"] = (
            str(_project_state_dir / "events.jsonl") if _project_state_dir is not None else ""
        )
        # Campaign lifetime metadata forwarded from the daemon namespace so the
        # Manager stage hook receives open_ended=True for daemon-created open-ended
        # campaigns, preventing final_stage_completion_decision from overwriting a
        # structured Manager rollback verdict with a bounded completion.
        config_kwargs["open_ended"] = bool(getattr(args, "open_ended", False))
        config_kwargs["continuous_objective"] = str(getattr(args, "continuous_objective", "") or "")
        # A paper contract is enabled only by a positively resolved
        # ``full_paper`` vertical.  An explicit False from a specialized caller
        # may still opt out; True cannot turn a non-paper vertical into a paper.
        _paper_override = getattr(args, "paper_mission", None)
        _paper_allowed = True if _paper_override is None else bool(_paper_override)
        config_kwargs["paper_mission"] = (
            not maintenance_mission and _paper_allowed and _paper_mission_for_project_root(_proot)
        )
        config_kwargs["workflow_mode"] = (
            "direct"
            if maintenance_mission
            else workflow_mode_override.strip().lower() or _workflow_mode_for_project_root(_proot)
        )
        try:
            from inspect import signature

            sig = signature(self._SkillLoopConfig)
            if not any(param.kind == param.VAR_KEYWORD for param in sig.parameters.values()):
                config_kwargs = {
                    key: value for key, value in config_kwargs.items() if key in sig.parameters
                }
        except (TypeError, ValueError):
            pass
        ex_state.workdir = workdir
        ex_state.effective_require_independent_review = effective_require_independent_review
        ex_state.config = self._SkillLoopConfig(**config_kwargs)
        ex_state.maintenance_checkpoint_dir = maintenance_checkpoint_dir

    def _build_execute_skill_store_and_loop(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
    ) -> None:
        """Refresh the Manager skill store, wire the per-round operator inbox
        drain, and construct this mission's ``SkillLoop``.
        """
        args = self._args
        workdir = ex_state.workdir
        config = ex_state.config
        self._refresh_manager_skill_store(args)
        # The per-project runtime state dir holds inbox.jsonl + events.jsonl.
        operator_state_dir = _project_state_dir_for(args, workdir)
        # REAL operator inbox (Change A): drain queued ``--notify`` / ``/nudge``
        # messages EACH engineer round — not just at mission start — so the
        # operator can steer a long in-flight mission instead of being locked out
        # until the next mission. Wired through the existing per-round
        # ``extra_guidance_provider`` hook; shares ``inbox.offset`` with the
        # supervisor's mission-start drain, so each message is delivered exactly
        # once with no duplication. Never raises into a mission.
        inbox_life_dir = operator_state_dir

        def _inbox_guidance_provider() -> list[str]:
            msgs: list[str] = []
            if inbox_life_dir is not None:
                try:
                    from ..skills.stage_machine import current_stage
                    from ._inbox import drain_inbox_messages

                    msgs.extend(
                        drain_inbox_messages(
                            inbox_life_dir,
                            current_stage=current_stage(workdir),
                        )
                    )
                except Exception:  # noqa: BLE001 — never break a mission
                    pass
            return msgs

        extra_guidance_provider = _inbox_guidance_provider if inbox_life_dir is not None else None
        engineer_backend = getattr(self, "engineer_backend", None) or self._backend
        global_skills_dir = Path(args.skills_dir)
        skill_store = None
        project_state_dir = str(getattr(args, "project_state_dir", "") or "").strip()
        if project_state_dir:
            from ..skills.layered import (
                LayeredSkillStore,
                shared_vertical_skills_dir,
            )
            from ..skills.vertical_select import _persisted_vertical

            active_vertical = _persisted_vertical(workdir) or ""

            skill_store = LayeredSkillStore(
                project_dir=Path(project_state_dir) / "skills",
                global_dir=global_skills_dir,
                vertical_dir=shared_vertical_skills_dir(
                    global_skills_dir,
                    active_vertical,
                ),
                runner=engineer_backend,
                matcher_model=config.resolved_matcher_model(),
                matcher_reasoning_effort=config.matcher_reasoning_effort,
            )
        ex_state.loop = self._SkillLoop(
            skills_dir=global_skills_dir,
            engineer_runner=engineer_backend,
            reviewer_runner=getattr(self, "reviewer_backend", None) or self._backend,
            config=config,
            skill_store=skill_store,
            on_event=sink.handle_event,
            extra_guidance_provider=extra_guidance_provider,
        )

    def _prepare_execute_mission_context(
        self,
        ex_state: "_ExecuteState",
        *,
        objective: str,
        prelude_context: str,
        seed_thread_id: str | None,
        scope: str,
    ) -> None:
        """Build the full task text (objective + prelude), pick the seed
        thread id to chain off of, and normalize the structural scope tag.
        """
        full_task = objective
        if prelude_context:
            full_task = f"{prelude_context}\n---\n## Live objective\n{objective}"
        # Use the seed for the first execute() of this runner; subsequent
        # execute() calls (LifeSupervisor may run several missions in one
        # supervisor.run()) chain off the previous mission's last thread_id.
        seed = self._next_seed_thread_id if seed_thread_id is None else seed_thread_id
        # Scope is threaded structurally from the planner via the backlog
        # item's tags (LifeSupervisor passes _planner_scope_from_item(item)).
        # We no longer re-parse it out of the objective prose — the harness
        # should consume the structured field, not sniff the rendered text.
        mission_scope = (scope or "").strip().lower()
        ex_state.full_task = full_task
        ex_state.seed = seed
        ex_state.mission_scope = mission_scope

    def _run_bounded_planning(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
        objective: str,
        original_objective: str,
        preplanned: bool,
    ) -> None:
        """Draft the advisory Planner execution plan for bounded (non-direct,
        non-preplanned) work and fold it into ``ex_state.full_task``.

        User-authored bounded work now follows the full team chain:
        Manager → Planner → Engineer → Reviewer. Planner-authored backlog
        items set ``preplanned=True`` and skip this call, avoiding a second
        redundant planning pass. The plan is advisory context, not a gate:
        if drafting fails, Engineer still receives the immutable objective.
        """
        args = self._args
        workdir = ex_state.workdir
        config = ex_state.config
        if preplanned or getattr(config, "workflow_mode", "staged") == "direct":
            return
        try:
            from ..manager.plan_mode import draft_plan
            from ..roles.prompts import resolve_role_prompt
            from ..roles.prompts.planner import preview_request

            planner_role_banner = resolve_role_prompt(
                preview_request(workdir)
            ).role_banner
            plan = draft_plan(
                getattr(self, "planner_backend", None) or self._backend,
                original_objective or objective,
                sink=sink,
                model=getattr(args, "plan_model", None),
                reasoning_effort=resolve_role_reasoning_effort(
                    "ARGUS_SKILL_PLANNER_REASONING_EFFORT"
                ),
                run_label="planner-bounded-plan",
                role_banner=planner_role_banner,
            )
            if plan.steps:
                lines = ["## Planner execution plan (advisory)"]
                for index, step in enumerate(plan.steps, 1):
                    detail = f" — {step.detail}" if step.detail else ""
                    lines.append(f"{index}. {step.title}{detail}")
                if plan.notes:
                    lines.append("Notes: " + "; ".join(plan.notes))
                ex_state.full_task += "\n\n---\n" + "\n".join(lines)
                sink.handle_event(
                    {
                        "type": "plan.completed",
                        "agent_layer": "planner",
                        "plan_mode": "bounded",
                        "steps": len(plan.steps),
                        "text": f"bounded execution plan · {len(plan.steps)} steps",
                    }
                )
            else:
                sink.handle_event(
                    {
                        "type": "life.planner.error",
                        "agent_layer": "planner",
                        "error": plan.error or "bounded plan unavailable",
                        "text": plan.error or "bounded plan unavailable; Engineer continues",
                    }
                )
        except Exception as exc:  # noqa: BLE001 — planning is advisory
            sink.handle_event(
                {
                    "type": "life.planner.error",
                    "agent_layer": "planner",
                    "error": f"{type(exc).__name__}: {exc}",
                    "text": "bounded plan unavailable; Engineer continues",
                }
            )

    def _invoke_execute_loop(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
        objective: str,
        original_objective: str,
        preplanned: bool,
        mission_id: str | None,
        usage_mission_id: str | None,
    ) -> None:
        """Run the mission through ``SkillLoop.run``, sandwiched between the
        advisory bounded-planning pass and this call's sink/usage-context
        teardown.
        """
        self._current_sink = sink
        self._current_failure_ledger = None
        self._set_usage_context(usage_mission_id or mission_id)
        try:
            self._run_bounded_planning(
                ex_state,
                sink=sink,
                objective=objective,
                original_objective=original_objective,
                preplanned=preplanned,
            )
            ex_state.outcome = ex_state.loop.run(
                ex_state.full_task,
                workdir=ex_state.workdir,
                seed_thread_id=ex_state.seed,
                objective_for_skill=objective,
                original_objective=original_objective or objective,
                scope=ex_state.mission_scope,
            )
        finally:
            self._current_sink = None
            self._current_failure_ledger = None
            self._set_usage_context(None)
            if ex_state.maintenance_checkpoint_dir is not None:
                try:
                    shutil.rmtree(ex_state.maintenance_checkpoint_dir)
                except OSError:
                    pass

    def _extract_execute_outcome_fields(self, ex_state: "_ExecuteState") -> None:
        """Update thread-id/auth-failure bookkeeping and pull the final
        round's reviewer verdict fields (planner report, harness control,
        failure attribution, final-submission certification, ...) off
        ``ex_state.outcome`` for the journal and the returned ``_Outcome``.
        """
        outcome = ex_state.outcome
        new_tid = getattr(outcome, "last_thread_id", None)
        if should_clear_thread_id_after_outcome(
            status=str(getattr(outcome, "status", "")),
            fatal_error=str(getattr(outcome, "stop_reason", "") or ""),
            stop_kind=getattr(outcome, "stop_kind", None),
        ):
            self.last_thread_id = None
            self._next_seed_thread_id = None
            new_tid = None
        elif new_tid:
            self.last_thread_id = new_tid
            self._next_seed_thread_id = new_tid
        auth_fail = self._consume_auth_failure()
        # Reviewer completion contract: certify whole-project completion only
        # from the final reviewer verdict (never raw success). Fail-closed:
        # absent rounds / review / non-final scope ⇒ not certified.
        final_submission_certified = False
        completion_evidence = ""
        # Pull the reviewer's structured planner briefing off the final round
        # so the supervisor can journal it for the project planner verbatim.
        planner_report: dict = {}
        harness_control: dict = {}
        checklist_feedback: dict = {}
        step_back: dict | None = None
        operator_question = ""
        research_result: dict = {}
        final_review_status = ""
        failure_source = ""
        failure_layer = ""
        validator_id = ""
        repair_paths: list[str] = []
        scientific_decision = ""
        review_source = ""
        rounds_list = getattr(outcome, "rounds", None) or []
        if rounds_list:
            _final_review = getattr(rounds_list[-1], "review", None)
            if _final_review is not None:
                final_review_status = (
                    str(getattr(_final_review, "status", "") or "").strip().lower()
                )
                failure_source = (
                    str(getattr(_final_review, "failure_source", "") or "").strip().lower()
                )
                failure_layer = (
                    str(getattr(_final_review, "failure_layer", "") or "").strip().lower()
                )
                validator_id = str(getattr(_final_review, "validator_id", "") or "").strip()
                repair_paths = list(getattr(_final_review, "repair_paths", []) or [])
                scientific_decision = normalize_research_direction(
                    getattr(_final_review, "scientific_decision", "")
                )
                review_source = str(getattr(_final_review, "review_source", "") or "").strip()
                report = getattr(_final_review, "planner_report", None)
                if isinstance(report, dict):
                    planner_report = report
                _harness_control = getattr(_final_review, "harness_control", None)
                if isinstance(_harness_control, dict):
                    harness_control = dict(_harness_control)
                _cfb = getattr(_final_review, "checklist_feedback", None)
                if isinstance(_cfb, dict) and _cfb:
                    checklist_feedback = _cfb
                _sb = getattr(_final_review, "step_back", None)
                if isinstance(_sb, dict) and _sb:
                    step_back = _sb
                operator_question = str(
                    getattr(_final_review, "operator_question", "") or ""
                ).strip()
                _research_result = getattr(_final_review, "research_result", None)
                if isinstance(_research_result, dict):
                    research_result = dict(_research_result)
        if ex_state.mission_scope == "final_submission":
            final_review = None
            if rounds_list:
                final_review = getattr(rounds_list[-1], "review", None)
            if final_review is not None and getattr(
                final_review, "final_submission_certified", False
            ):
                final_submission_certified = True
                completion_evidence = getattr(final_review, "reason", "")
        ex_state.new_tid = new_tid
        ex_state.auth_fail = auth_fail
        ex_state.rounds_list = rounds_list
        ex_state.planner_report = planner_report
        ex_state.harness_control = harness_control
        ex_state.checklist_feedback = checklist_feedback
        ex_state.step_back = step_back
        ex_state.operator_question = operator_question
        ex_state.research_result = research_result
        ex_state.final_review_status = final_review_status
        ex_state.failure_source = failure_source
        ex_state.failure_layer = failure_layer
        ex_state.validator_id = validator_id
        ex_state.repair_paths = repair_paths
        ex_state.scientific_decision = scientific_decision
        ex_state.review_source = review_source
        ex_state.final_submission_certified = final_submission_certified
        ex_state.completion_evidence = completion_evidence

    def _maybe_decide_stage_transition(
        self,
        ex_state: "_ExecuteState",
        *,
        sink: EventSink,
        mission_id: str | None,
        usage_mission_id: str | None,
        maintenance_mission: bool,
    ) -> None:
        """Hand this round's structured completion verdict to the Manager's
        stage authority when this round is eligible to move the pipeline stage.

        STAGE AUTHORITY: the Manager is the SOLE post-bootstrap writer of the
        pipeline stage. After this round's Engineer-self-review or independent
        Reviewer verdict, the Manager makes
        its OWN judgment (advance / hold / rollback) and writes
        PIPELINE_STATE.json. See ``_decide_stage_transition``.
        """
        outcome = ex_state.outcome
        effective_status = str(outcome.status)
        effective_stop_kind = getattr(outcome, "stop_kind", None)
        effective_recoverable = bool(getattr(outcome, "recoverable", False))
        effective_reason = outcome.reason or ""
        stage_transition: dict = {}
        # Direct workflow skips an extra planning pass, not Manager stage
        # authority. A required independent Reviewer verdict must reach the
        # stage writer before any planner-wait reconciliation.
        if not maintenance_mission and _should_run_stage_transition(
            effective_status,
            ex_state.planner_report,
            harness_control=ex_state.harness_control,
            mission_scope=ex_state.mission_scope,
            require_independent_review=ex_state.effective_require_independent_review,
            review_source=ex_state.review_source,
        ):
            self._current_sink = sink
            self._set_usage_context(usage_mission_id or mission_id)
            try:
                stage_transition = self._decide_stage_transition(
                    rounds_list=ex_state.rounds_list,
                    workdir=ex_state.workdir,
                    sink=sink,
                    root_task_id=usage_mission_id or mission_id,
                    mission_scope=ex_state.mission_scope,
                    open_ended=bool(getattr(ex_state.config, "open_ended", False)),
                    continuous_objective=str(
                        getattr(ex_state.config, "continuous_objective", "") or ""
                    ),
                )
            finally:
                self._current_sink = None
                self._set_usage_context(None)
        ex_state.effective_status = effective_status
        ex_state.effective_stop_kind = effective_stop_kind
        ex_state.effective_recoverable = effective_recoverable
        ex_state.effective_reason = effective_reason
        ex_state.stage_transition = stage_transition

    def _build_execute_outcome(self, ex_state: "_ExecuteState") -> _Outcome:
        """Assemble the ``_Outcome`` returned to the caller from the fields
        gathered across the prior lifecycle phases.
        """
        outcome = ex_state.outcome
        return _Outcome(
            success=bool(outcome.successful and ex_state.effective_status == "done"),
            status=ex_state.effective_status,
            stop_reason=ex_state.effective_reason,
            stop_kind=ex_state.effective_stop_kind,
            recoverable=ex_state.effective_recoverable,
            rounds=outcome.round_count,
            matched_skill_name=outcome.skill_used,
            skill_distilled=outcome.skill_distilled,
            last_thread_id=ex_state.new_tid,
            auth_failure=ex_state.auth_fail,
            final_submission_certified=ex_state.final_submission_certified,
            completion_evidence=ex_state.completion_evidence,
            planner_report=ex_state.planner_report,
            harness_control=ex_state.harness_control,
            checklist_feedback=ex_state.checklist_feedback,
            step_back=ex_state.step_back,
            stage_transition=ex_state.stage_transition,
            operator_question=ex_state.operator_question,
            research_result=ex_state.research_result,
            final_review_status=ex_state.final_review_status,
            failure_source=ex_state.failure_source,
            failure_layer=ex_state.failure_layer,
            validator_id=ex_state.validator_id,
            repair_paths=ex_state.repair_paths,
            scientific_decision=ex_state.scientific_decision,
        )
