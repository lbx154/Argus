"""argus.manager._vertical_ops — mixin for the Manager's vertical-decision methods.

``_VerticalDecisionMixin`` carries every method that selects, commits, and
maintains the active vertical / data domain.  It references ``self`` attributes
set by ``Manager.__init__`` (project_root, runner, _session, mission,
manager_session_root) and imports helpers from ``_helpers`` and ``_session_ops``
to avoid circular imports with ``_core``.
"""
from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from typing import Any

from ..skills import vertical_select
from ..skills.vertical_select import (
    persist_vertical,
)
from ._helpers import (
    _DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS,
    _manager_backend_failure,
    _manager_model,
    _manager_reasoning_effort,
    _manager_route_positive_int,
    _manager_vertical_reasoning_effort,
    gateway_run_exec,
    log,
)
from ._session_ops import _restore_files_on_error
from .domain_author import VerticalDecision, VerticalDecisionError

_log = logging.getLogger(__name__)


def _repository_workflow_mode(mode: str) -> str:
    require_planner = (
        os.environ.get("ARGUS_SKILL_SOFTWARE_REQUIRE_PLANNER", "0")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    return "staged" if require_planner else mode


def _software_grounding_required(workflow_mode: str) -> bool:
    raw = os.environ.get("ARGUS_SKILL_SOFTWARE_REQUIRE_GROUNDING", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return workflow_mode != "direct"


_CURRENT_OPERATOR_MARKER = "[CURRENT OPERATOR MESSAGE]"


class _VerticalDecisionMixin:
    """Mixin: vertical selection, staging, and domain-commit methods."""

    def _ground_execution_task(
        self,
        task: str,
        *,
        workflow_mode: str,
        root_task_id: str | None,
    ) -> str:
        """Attach a bounded repository-grounding brief before code handoff."""
        from ..core.models import RunnerOptions
        from ..core.role_slots import role_call_slot
        from .stage_decider import extract_answer

        if not _software_grounding_required(workflow_mode):
            return task.strip()
        if self.runner is None:
            return task.strip()
        manager_libraries = self.mission.libraries()
        prompt = (
            f"{manager_libraries.block}\n\n" if manager_libraries.block else ""
        ) + (
            "Ground this repository task with repository tools before handoff. "
            "Search the Manager-owned Skill paths above first and read a clearly "
            "relevant grounding Skill on demand if one exists. The tool working "
            "directory is already the repository root: use relative paths, never "
            "guess another checkout path, and never search the filesystem root. "
            "Return only a compact human-readable grounding brief with: "
            "architecture/call path, closest unchanged analogue, affected "
            "callers and compatibility surfaces, exact build/test commands, "
            "held-back acceptance risks, and recommended decomposition for "
            f"workflow_mode={workflow_mode}. Do not modify files, solve the task, "
            "or invent requirements.\n\n"
            f"## Operator task\n{task.strip()}"
        )
        try:
            with (
                self._task_usage_scope(root_task_id),
                role_call_slot("project_grounding"),
            ):
                result = gateway_run_exec(
                    self.runner,
                    prompt=prompt,
                    options=RunnerOptions(
                        model=_manager_model(),
                        reasoning_effort=os.environ.get(
                            "ARGUS_SKILL_MANAGER_GROUNDING_REASONING_EFFORT",
                            "low",
                        ),
                        working_dir=str(self.execution_workdir),
                        skill_paths=[
                            str(path) for path in manager_libraries.native_paths
                        ],
                        sandbox_mode="read-only",
                        dangerous_yolo=False,
                        skip_git_repo_check=True,
                    ),
                    run_label="manager-project-grounding",
                )
        except Exception:  # noqa: BLE001 - grounding is evidence, not admission
            log.debug("Manager software grounding call failed", exc_info=True)
            return task.strip()
        failed, _detail = _manager_backend_failure(result)
        brief = extract_answer(result).strip()
        if failed:
            return task.strip()
        if not brief:
            return task.strip()
        if len(brief) > 8_000:
            brief = brief[:7_999].rstrip() + "…"
        return (
            task.strip()
            + "\n\n## Manager project grounding (advisory evidence)\n"
            + brief
        )

    # ---- the Manager's grounded vertical decision (agent, not keywords) ----
    def _decide_research_target(
        self,
        task: str,
        *,
        root_task_id: str | None,
        supported_levels: tuple[str, ...],
    ) -> str:
        """Decide the success bar when the operator fixed a research vertical."""
        from ..core.research_contract import research_target_env_override

        try:
            override = research_target_env_override()
        except ValueError as exc:
            raise VerticalDecisionError(str(exc)) from exc
        if override is not None:
            if override not in supported_levels:
                raise VerticalDecisionError(
                    f"research target {override!r} is not supported by this vertical"
                )
            return override
        backend = self._session or self.runner
        if backend is None:
            conservative_target = supported_levels[-1]
            log.warning(
                "explicit research vertical has no Manager backend; defaulting "
                "research_target_level to %s so enqueue remains available "
                "without permitting an unclassified success",
                conservative_target,
            )
            return conservative_target
        from ..core.models import RunnerOptions
        from ..roles.prompts.manager import build_research_target_prompt
        from .domain_author import parse_research_target_level
        from .stage_decider import extract_answer

        with self._task_usage_scope(root_task_id):
            result = gateway_run_exec(
                backend,
                prompt=build_research_target_prompt(
                    task,
                    supported_levels=supported_levels,
                ),
                options=RunnerOptions(
                    model=_manager_model(),
                    reasoning_effort=_manager_reasoning_effort(),
                    working_dir=str(self.execution_workdir),
                    dangerous_yolo=True,
                    skip_git_repo_check=True,
                ),
                run_label="manager-research-target",
            )
        failed, detail = _manager_backend_failure(result)
        if failed:
            raise VerticalDecisionError(
                "Manager research-target backend failed"
                + (f": {detail}" if detail else "")
            )
        target_level = parse_research_target_level(
            extract_answer(result),
            supported_levels=supported_levels,
        )
        if target_level is None:
            raise VerticalDecisionError(
                "Manager did not produce a valid research_target_level"
            )
        return target_level

    def decide_vertical(
        self,
        task: str,
        *,
        root_task_id: str | None = None,
    ) -> VerticalDecision:
        """Choose the vertical for ``task``.

        Every formal task is classified by the Manager itself after mandatory,
        bounded repository inspection. A vertical decision without observed tool
        activity is rejected rather than persisted, even when the model claims a
        confident existing-vertical match.

        FAIL-HARD when agent judgment is needed: no backend, or a model reply that
        is missing / not a valid choice, RAISES ``VerticalDecisionError``. There is
        NO keyword classifier and NO silent fallback to the research default.
        """
        # Routing is intentionally isolated from the persistent Manager chat
        # session. Reusing prior conversation would violate the fast pass's
        # strict context bound and make cost depend on unrelated earlier turns.
        backend = self.runner
        if backend is None:
            raise VerticalDecisionError(
                "cannot decide the vertical: the Manager has no backend/runner"
            )
        from ..core.models import RunnerOptions
        from ..domains import BUILTIN_DOMAINS, DOMAIN_PURPOSES
        from ..roles.prompts.manager import build_vertical_decision_prompt
        from ..verticals._data_domain import (
            list_all_data_domain_names,
            list_selectable_data_domain_summaries,
        )
        from .domain_author import parse_vertical_decision
        from .stage_decider import extract_answer

        existing_summaries = list_selectable_data_domain_summaries(
            self.project_root,
            learned_root=self.learned_vertical_root,
        )
        existing = tuple(existing_summaries)
        all_domain_names = list_all_data_domain_names(
            self.project_root,
            learned_root=self.learned_vertical_root,
        )
        contextual_task = (
            "[CURRENT OPERATOR MESSAGE]" in task
            and (
                "[RECENT CONVERSATION CONTEXT" in task
                or "[BOUNDED TASK CONTEXT" in task
            )
        )
        from ..verticals._base import (
            load_vertical,
            vertical_research_target_levels,
        )

        research_target_verticals = tuple(
            name
            for name in vertical_select.available_verticals()
            if vertical_research_target_levels(
                load_vertical(name, project_root=self.project_root)
            )
        )
        backend_name = str(
            getattr(backend, "_backend_name", "")
            or getattr(self.runner, "_backend_name", "")
            or ""
        ).strip().lower()

        with self._task_usage_scope(root_task_id):
            prompt = build_vertical_decision_prompt(
                task,
                verticals_with_purpose=vertical_select.available_vertical_purposes(),
                domains_with_purpose=DOMAIN_PURPOSES,
                existing_data_domains=existing,
                existing_data_domain_summaries=existing_summaries,
                research_target_verticals=research_target_verticals,
            )
            grounded_prompt_limit = _manager_route_positive_int(
                "ARGUS_SKILL_MANAGER_GROUNDED_ROUTE_MAX_PROMPT_CHARS",
                _DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS,
            )
            if len(prompt) > grounded_prompt_limit:
                raise VerticalDecisionError(
                    "Manager grounded-route prompt exceeds configured context cap "
                    f"({len(prompt)} > {grounded_prompt_limit} characters)"
                )
            grounded_extra_args = (
                [
                    "--no-custom-instructions",
                    "--disable-builtin-mcps",
                    "--context",
                    "default",
                ]
                if backend_name == "copilot"
                else None
            )
            result = gateway_run_exec(
                backend,
                prompt=prompt,
                options=RunnerOptions(
                    model=_manager_model(),
                    reasoning_effort=_manager_vertical_reasoning_effort(),
                    working_dir=str(self.execution_workdir),
                    dangerous_yolo=True,
                    skip_git_repo_check=True,
                    extra_args=grounded_extra_args,
                ),
                run_label="manager-classify-grounded",
            )
        failed, detail = _manager_backend_failure(result)
        if failed:
            raise VerticalDecisionError(
                "Manager grounded-route backend failed"
                + (f": {detail}" if detail else "")
            )
        if not bool(getattr(result, "tool_activity_observed", False)):
            raise VerticalDecisionError(
                "Manager grounded vertical decision did not inspect repository tools"
            )
        answer = extract_answer(result)
        decision = parse_vertical_decision(
            answer,
            known_verticals=list(vertical_select.available_verticals()),
            known_domains=list(BUILTIN_DOMAINS),
            existing_data_domains=all_domain_names,
            research_target_verticals=research_target_verticals,
            default_execution_task="" if contextual_task else task.strip(),
        )
        if decision is None:
            raise VerticalDecisionError(
                f"Manager could not decide a vertical for task {task!r}: the "
                "model reply was missing or not a valid existing/new choice"
            )
        if decision.choice == "existing":
            from ..verticals._base import load_vertical_contract
            from ..verticals._data_domain import materialize_learned_data_domain

            materialize_learned_data_domain(
                self.learned_vertical_root,
                self.project_root,
                decision.vertical,
            )
            contract = load_vertical_contract(
                decision.vertical,
                project_root=self.project_root,
            )
            if contract.mission_kind == "software":
                decision.workflow_mode = _repository_workflow_mode(
                    decision.workflow_mode
                )
            if (
                contract.ground_before_handoff
                and _software_grounding_required(decision.workflow_mode)
            ):
                decision.execution_task = self._ground_execution_task(
                    decision.execution_task,
                    workflow_mode=decision.workflow_mode,
                    root_task_id=root_task_id,
                )
        if contextual_task and (
            "[RECENT CONVERSATION CONTEXT" in decision.execution_task
            or "[BOUNDED TASK CONTEXT" in decision.execution_task
            or _CURRENT_OPERATOR_MARKER in decision.execution_task
        ):
            raise VerticalDecisionError(
                "Manager execution_task copied bounded conversation context "
                "instead of producing a standalone handoff"
            )
        return decision

    def _apply_vertical_decision_rendering(
        self,
        decision: VerticalDecision,
    ) -> None:
        """Apply Manager-owned presentation only after its decision commits."""
        try:
            from .live_view import apply_manager_rendering_response

            apply_manager_rendering_response(
                self.execution_workdir,
                decision.rendering_response,
                manifest_root=self.manager_session_root,
                null_means_clear=True,
            )
        except Exception:  # noqa: BLE001
            log.debug("manager live-view persistence failed", exc_info=True)

    @staticmethod
    def _kind_for(vertical: str) -> str:
        """Return the provider-declared coarse mission kind."""
        from ..verticals._base import load_vertical_contract

        try:
            return load_vertical_contract(vertical).mission_kind
        except LookupError:
            return "custom"  # project-local data domains need a project root

    # ---- split into the vertical's Stage template ----
    def plan_stages(self, vertical: str) -> list[str]:
        """The vertical's Stage list (research → the 8-stage paper pipeline).

        Reads the validated vertical contract. Missing stages or a broken
        provider fail visibly; substituting another vertical would change the
        task and is never a recovery strategy.
        """
        from ..verticals._base import load_vertical_contract

        return list(
            load_vertical_contract(
                vertical,
                project_root=self.project_root,
            ).stage_order
        )

    # ---- the user-facing division step ----
    def divide(
        self,
        task: str,
        *,
        ask_on_new_domain: bool = False,
        root_task_id: str | None = None,
    ) -> Any:
        """Decide the vertical (Manager agent) → stages → COMMIT so the existing
        supervisor trusts it (no re-classify). Returns the Division for
        display/confirmation.

        * existing built-in vertical or existing data domain → persist it.
        * new data domain → ``ask_on_new_domain`` controls the commit:
          * ``False`` (autonomous): write the data domain + persist immediately.
          * ``True`` (ask): return a ``Division`` carrying the proposal with
            ``pending_confirmation=True`` and write NOTHING — the caller confirms
            with the operator and then calls :meth:`commit_domain`.

        FAIL-HARD: a blank task or an undecidable vertical RAISES. There is no
        silent fallback to the research default.

        This is also the layer where a genuinely NEW, operator-issued intent is
        dispatched, so — right after persisting the decided vertical — it
        checks whether the PREVIOUSLY-persisted vertical had already reached
        ITS OWN terminal stage with ``status="done"``. If so, the old run is
        finished and this call is superseding it with new work: ``current_stage``
        is reset to the selected vertical's first stage even when the new task
        uses the same vertical (via
        ``vertical_select.reset_stage_for_new_intent`` /
        ``stage_machine.rollback_stage``) instead of silently inheriting a
        stale terminal stage. This does NOT touch ``persist_vertical``'s
        seed-only, never-reset contract for the (common) in-project
        reclassification case, where the prior vertical was not yet finished.
        """
        if not (task and task.strip()):
            raise ValueError("Manager.divide requires a non-empty task")
        decision = self.decide_vertical(task, root_task_id=root_task_id)
        return self.commit_vertical_decision(
            task,
            decision,
            ask_on_new_domain=ask_on_new_domain,
        )

    def commit_vertical_decision(
        self,
        task: str,
        decision: VerticalDecision,
        *,
        ask_on_new_domain: bool = False,
        force_stage_reset: bool = False,
        _lock_held: bool = False,
    ) -> Any:
        """Commit a previously computed decision without another model call."""
        lock = nullcontext() if _lock_held else self.pipeline_lock()
        with lock:
            return self._commit_vertical_decision_locked(
                task,
                decision,
                ask_on_new_domain=ask_on_new_domain,
                force_stage_reset=force_stage_reset,
            )

    def _commit_vertical_decision_locked(
        self,
        task: str,
        decision: VerticalDecision,
        *,
        ask_on_new_domain: bool,
        force_stage_reset: bool = False,
    ) -> Any:
        # Import Division lazily to avoid the circular import with _core.
        from ._core import Division

        old_vertical = vertical_select._persisted_vertical(self.project_root)
        if decision.choice == "new":
            proposal = decision.proposal
            if ask_on_new_domain:
                division = Division(
                    task=task, vertical=proposal.name, kind="custom",
                    stages=list(proposal.stages),
                    domain="",
                    workflow_mode=decision.workflow_mode,
                    execution_task=decision.execution_task,
                    proposed_domain=proposal, pending_confirmation=True,
                )
                self._apply_vertical_decision_rendering(decision)
                return division
            division = self._commit_domain_locked(
                task,
                proposal,
                _old_vertical=old_vertical,
                execution_task=decision.execution_task,
                workflow_mode=decision.workflow_mode,
            )
            if force_stage_reset:
                vertical_select.reset_stage_for_new_intent(
                    self.project_root,
                    old_vertical=old_vertical,
                    new_vertical=division.vertical,
                    force_replacement=True,
                    evidence_root=self.execution_workdir,
                )
            self._apply_vertical_decision_rendering(decision)
            return division
        vertical = decision.vertical
        from ..verticals._data_domain import (
            load_data_domain,
            materialize_learned_data_domain,
            revise_data_domain_stages,
        )

        materialize_learned_data_domain(
            self.learned_vertical_root,
            self.project_root,
            vertical,
        )
        pipeline_state = self.project_root / "research" / "PIPELINE_STATE.json"
        domain_path = (
            self.project_root / "research" / "DOMAINS" / f"{vertical}.json"
        )
        index_path = self.project_root / "research" / "DOMAINS" / "INDEX.json"
        adapted = bool(
            decision.adapted_stages
            and load_data_domain(vertical, self.project_root) is not None
        )
        restore_paths = [pipeline_state]
        if adapted:
            restore_paths.extend((domain_path, index_path))
        refresh_research_target_epoch = bool(
            force_stage_reset
            or adapted
            or (
                old_vertical
                and vertical_select.vertical_reached_own_terminal_stage(
                    self.project_root,
                    old_vertical,
                )
            )
        )
        with _restore_files_on_error(restore_paths):
            if adapted:
                revise_data_domain_stages(
                    self.project_root,
                    vertical,
                    stages=decision.adapted_stages,
                    reason=decision.adaptation_reason or task,
                )
            stages = self.plan_stages(vertical)
            persist_vertical(
                self.project_root,
                vertical,
                domain=decision.domain or None,
                research_target_level=decision.research_target_level or None,
                workflow_mode=decision.workflow_mode,
                target_venue=decision.target_venue or None,
                refresh_research_target_epoch=refresh_research_target_epoch,
            )
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=old_vertical,
                new_vertical=vertical,
                force_replacement=force_stage_reset or adapted,
                evidence_root=self.execution_workdir,
            )
        division = Division(
            task=task,
            vertical=vertical,
            domain=decision.domain,
            kind=self._kind_for(vertical),
            stages=stages,
            workflow_mode=decision.workflow_mode,
            execution_task=decision.execution_task,
            learned_vertical_status=(
                getattr(
                    load_data_domain(vertical, self.project_root),
                    "status",
                    "",
                )
                if vertical not in vertical_select.VERTICALS
                else ""
            ),
        )
        self._apply_vertical_decision_rendering(decision)
        return division

    def commit_domain(
        self,
        task: str,
        proposal: Any,
        *,
        _old_vertical: str | None = None,
        execution_task: str = "",
        workflow_mode: str = "staged",
        _lock_held: bool = False,
    ) -> Any:
        """Write the authored data domain to disk and persist it as the active
        vertical (so the supervisor trusts it). FAIL-HARD: a write error
        PROPAGATES — no silent research fallback. Called autonomously by
        :meth:`divide` or by the cockpit after operator confirmation.

        ``_old_vertical`` (private, optional) lets :meth:`divide` pass along the
        vertical it read BEFORE deciding — so the new-intent-supersedes-a-
        finished-vertical stage reset (see :meth:`divide`'s docstring) still
        applies on the new-data-domain path. When called directly (e.g. by the
        cockpit after an operator confirms a pending proposal) it is re-read here.
        """
        lock = nullcontext() if _lock_held else self.pipeline_lock()
        with lock:
            return self._commit_domain_locked(
                task,
                proposal,
                _old_vertical=_old_vertical,
                execution_task=execution_task,
                workflow_mode=workflow_mode,
            )

    def _commit_domain_locked(
        self,
        task: str,
        proposal: Any,
        *,
        _old_vertical: str | None,
        execution_task: str,
        workflow_mode: str,
    ) -> Any:
        from ..verticals._data_domain import write_data_domain
        from ._core import Division

        if _old_vertical is None:
            _old_vertical = vertical_select._persisted_vertical(self.project_root)

        pipeline_state = self.project_root / "research" / "PIPELINE_STATE.json"
        domain_path = (
            self.project_root
            / "research"
            / "DOMAINS"
            / f"{proposal.name}.json"
        )
        with _restore_files_on_error([pipeline_state, domain_path]):
            write_data_domain(
                self.project_root,
                proposal.name,
                stages=list(proposal.stages),
                created_by="manager",
                status="candidate",
                purpose=(
                    str(getattr(proposal, "rationale", "") or "").strip()
                    or execution_task.strip()
                    or str(getattr(proposal, "execution_task", "") or "").strip()
                    or task.strip()
                ),
                require_independent_review=True,
            )
            persist_vertical(
                self.project_root,
                proposal.name,
                workflow_mode=workflow_mode,
            )
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=_old_vertical,
                new_vertical=proposal.name,
                evidence_root=self.execution_workdir,
            )
        return Division(
            task=task, vertical=proposal.name, kind="custom",
            stages=list(proposal.stages), proposed_domain=proposal,
            execution_task=(
                execution_task
                or str(getattr(proposal, "execution_task", "") or "")
            ),
            workflow_mode=workflow_mode,
            pending_confirmation=False,
            learned_vertical_status="candidate",
        )
