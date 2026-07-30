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
import time
from contextlib import nullcontext
from typing import Any

from ..skills import vertical_select
from ..skills.vertical_select import (
    persist_vertical,
)
from ._helpers import (
    _DEFAULT_FAST_ROUTE_MAX_PROMPT_CHARS,
    _DEFAULT_FAST_ROUTE_MAX_TASK_CHARS,
    _DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS,
    _OPTIMIZE_VERTICALS,
    _manager_backend_failure,
    _manager_fast_route_enabled,
    _manager_fast_route_min_confidence,
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


def _software_workflow_mode(mode: str) -> str:
    require_planner = (
        os.environ.get("ARGUS_SKILL_SOFTWARE_REQUIRE_PLANNER", "0")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    return "staged" if require_planner else mode


class _VerticalDecisionMixin:
    """Mixin: vertical selection, staging, and domain-commit methods."""

    def _ground_software_execution_task(
        self,
        task: str,
        *,
        workflow_mode: str,
        root_task_id: str | None,
        deadline: float | None = None,
    ) -> str:
        """Attach a bounded repository-grounding brief to software handoff."""
        from ..core.models import RunnerOptions
        from ..core.role_slots import role_call_slot
        from ..skills.builtins import iter_vertical_skill_texts
        from .stage_decider import extract_answer

        skill = dict(iter_vertical_skill_texts("software")).get(
            "manager/software-project-grounding.md",
            "",
        )
        if not skill or self.runner is None:
            return task.strip()
        prompt = (
            f"{skill}\n\n"
            "Apply this grounding skill now with repository tools. "
            "The tool working directory is already the repository root: use "
            "relative paths, never guess another checkout path, and never search "
            "the filesystem root. Return only a compact human-readable grounding "
            "brief with: "
            "architecture/call path, closest unchanged analogue, affected "
            "callers and compatibility surfaces, exact build/test commands, "
            "held-back acceptance risks, and recommended decomposition for "
            f"workflow_mode={workflow_mode}. Do not modify files, solve the task, "
            "or invent requirements.\n\n"
            f"## Operator task\n{task.strip()}"
        )
        try:
            max_seconds = max(
                30,
                int(
                    os.environ.get(
                        "ARGUS_SKILL_MANAGER_GROUNDING_MAX_SECONDS",
                        "300",
                    )
                ),
            )
        except ValueError:
            max_seconds = 300
        try:
            with (
                self._task_usage_scope(root_task_id),
                role_call_slot("project_grounding"),
            ):
                grounding_deadline = time.monotonic() + max_seconds
                if deadline is not None and deadline > time.monotonic():
                    grounding_deadline = min(grounding_deadline, deadline)
                result = gateway_run_exec(
                    self.runner,
                    prompt=prompt,
                    options=RunnerOptions(
                        model=_manager_model(),
                        reasoning_effort=os.environ.get(
                            "ARGUS_SKILL_MANAGER_GROUNDING_REASONING_EFFORT",
                            "low",
                        ),
                        working_dir=str(self.project_root),
                        dangerous_yolo=True,
                        skip_git_repo_check=True,
                        external_interrupt_reason_provider=lambda: (
                            "Manager project grounding time budget reached"
                            if time.monotonic() >= grounding_deadline
                            else None
                        ),
                    ),
                    run_label="manager-project-grounding",
                )
        except Exception:  # noqa: BLE001 - grounding is evidence, not admission
            log.debug("Manager software grounding call failed", exc_info=True)
            return task.strip()
        failed, _detail = _manager_backend_failure(result)
        brief = extract_answer(result).strip()
        if failed and not brief:
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
                    working_dir=str(self.project_root),
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

        Every formal task is classified by the Manager itself. A compact,
        tool-free model request chooses a clear existing vertical directly. Invalid,
        low-confidence, explicitly uncertain, or potentially-new-domain answers
        escalate once to the bounded grounded repository-inspection prompt.

        Fast routing does not choose Live View files or rewrite the task. The
        original operator task becomes the Planner/Engineer handoff; later Manager
        stage/chat decisions retain ownership of presentation choices.

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
        try:
            total_seconds = max(
                30,
                int(
                    os.environ.get(
                        "ARGUS_SKILL_MANAGER_TOTAL_MAX_SECONDS",
                        "240",
                    )
                ),
            )
        except ValueError:
            total_seconds = 240
        route_deadline = time.monotonic() + total_seconds

        def route_interrupt() -> str | None:
            if time.monotonic() >= route_deadline:
                return "Manager routing and grounding total budget reached"
            return None
        from ..core.models import RunnerOptions
        from ..domains import BUILTIN_DOMAINS, DOMAIN_PURPOSES
        from ..roles.prompts.manager import (
            build_fast_vertical_decision_prompt,
            build_vertical_decision_prompt,
        )
        from ..verticals._data_domain import list_data_domains
        from .domain_author import (
            parse_fast_vertical_decision,
            parse_vertical_decision,
        )
        from .stage_decider import extract_answer

        existing = list_data_domains(self.project_root)
        from ..verticals._base import (
            load_vertical,
            vertical_research_target_levels,
        )

        research_target_verticals = tuple(
            name
            for name in vertical_select.VERTICALS
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
            fast_prompt = ""
            if (
                _manager_fast_route_enabled()
                and len((task or "").strip())
                <= _manager_route_positive_int(
                    "ARGUS_SKILL_MANAGER_FAST_ROUTE_MAX_TASK_CHARS",
                    _DEFAULT_FAST_ROUTE_MAX_TASK_CHARS,
                )
            ):
                fast_prompt = build_fast_vertical_decision_prompt(
                    task,
                    verticals_with_purpose=vertical_select.VERTICAL_PURPOSES,
                    domains_with_purpose=DOMAIN_PURPOSES,
                    existing_data_domains=existing,
                    research_target_verticals=research_target_verticals,
                )
            fast_prompt_limit = _manager_route_positive_int(
                "ARGUS_SKILL_MANAGER_FAST_ROUTE_MAX_PROMPT_CHARS",
                _DEFAULT_FAST_ROUTE_MAX_PROMPT_CHARS,
            )
            if fast_prompt and len(fast_prompt) <= fast_prompt_limit:
                fast_extra_args = None
                fast_sandbox = "read-only"
                if backend_name == "copilot":
                    # No tools means Copilot cannot turn this classification into
                    # a repository-audit loop. ``--context default`` prevents a
                    # persisted long-context preference from inflating the call.
                    fast_sandbox = None
                    fast_extra_args = [
                        "--no-custom-instructions",
                        "--disable-builtin-mcps",
                        "--available-tools=",
                        "--context",
                        "default",
                    ]
                fast_result = gateway_run_exec(
                    backend,
                    prompt=fast_prompt,
                    options=RunnerOptions(
                        model=_manager_model(),
                        reasoning_effort=_manager_vertical_reasoning_effort(),
                        working_dir=str(self.project_root),
                        sandbox_mode=fast_sandbox,
                        skip_git_repo_check=True,
                        extra_args=fast_extra_args,
                        external_interrupt_reason_provider=route_interrupt,
                    ),
                    run_label="manager-classify-fast",
                )
                fast_failed, fast_detail = _manager_backend_failure(fast_result)
                if fast_failed:
                    raise VerticalDecisionError(
                        "Manager fast-route backend failed"
                        + (f": {fast_detail}" if fast_detail else "")
                    )
                fast_route = parse_fast_vertical_decision(
                    extract_answer(fast_result),
                    known_verticals=list(vertical_select.VERTICALS),
                    known_domains=list(BUILTIN_DOMAINS),
                    existing_data_domains=existing,
                    research_target_verticals=research_target_verticals,
                )
                if (
                    fast_route is not None
                    and not fast_route.needs_grounding
                    and fast_route.confidence >= _manager_fast_route_min_confidence()
                ):
                    workflow_mode = fast_route.workflow_mode
                    if fast_route.vertical == "software":
                        workflow_mode = _software_workflow_mode(workflow_mode)
                    execution_task = task.strip()
                    if fast_route.vertical == "software":
                        execution_task = self._ground_software_execution_task(
                            task,
                            workflow_mode=workflow_mode,
                            root_task_id=root_task_id,
                            deadline=route_deadline,
                        )
                    return VerticalDecision(
                        choice="existing",
                        vertical=fast_route.vertical,
                        domain=fast_route.domain,
                        workflow_mode=workflow_mode,
                        execution_task=execution_task,
                        research_target_level=fast_route.research_target_level,
                        target_venue=fast_route.target_venue,
                    )
                log.info(
                    "Manager fast route escalated to grounded routing: %s",
                    (
                        fast_route.rationale
                        if fast_route is not None and fast_route.rationale
                        else "invalid or low-confidence fast-route response"
                    ),
                )
            elif fast_prompt:
                log.info(
                    "Manager fast route skipped because prompt exceeded %d chars",
                    fast_prompt_limit,
                )

            prompt = build_vertical_decision_prompt(
                task,
                verticals_with_purpose=vertical_select.VERTICAL_PURPOSES,
                domains_with_purpose=DOMAIN_PURPOSES,
                existing_data_domains=existing,
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
                    working_dir=str(self.project_root),
                    dangerous_yolo=True,
                    skip_git_repo_check=True,
                    extra_args=grounded_extra_args,
                    external_interrupt_reason_provider=route_interrupt,
                ),
                run_label="manager-classify-grounded",
            )
        failed, detail = _manager_backend_failure(result)
        if failed:
            raise VerticalDecisionError(
                "Manager grounded-route backend failed"
                + (f": {detail}" if detail else "")
            )
        answer = extract_answer(result)
        decision = parse_vertical_decision(
            answer,
            known_verticals=list(vertical_select.VERTICALS),
            known_domains=list(BUILTIN_DOMAINS),
            existing_data_domains=existing,
            research_target_verticals=research_target_verticals,
            default_execution_task=task.strip(),
        )
        if decision is None:
            raise VerticalDecisionError(
                f"Manager could not decide a vertical for task {task!r}: the "
                "model reply was missing or not a valid existing/new choice"
            )
        if decision.vertical == "software":
            decision.workflow_mode = _software_workflow_mode(
                decision.workflow_mode
            )
            decision.execution_task = self._ground_software_execution_task(
                task,
                workflow_mode=decision.workflow_mode,
                root_task_id=root_task_id,
                deadline=route_deadline,
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
                self.project_root,
                decision.rendering_response,
                manifest_root=self.manager_session_root,
                null_means_clear=True,
            )
        except Exception:  # noqa: BLE001
            log.debug("manager live-view persistence failed", exc_info=True)

    @staticmethod
    def _kind_for(vertical: str) -> str:
        """Coarse kind for a resolved vertical: optimize | research | custom."""
        if vertical in _OPTIMIZE_VERTICALS:
            return "optimize"
        if vertical in ("research", "quant"):
            return "research"
        if vertical == "software":
            return "software"
        return "custom"  # a project-local (Manager-authored) data domain

    # ---- split into the vertical's Stage template ----
    def plan_stages(self, vertical: str) -> list[str]:
        """The vertical's Stage list (research → the 8-stage paper pipeline).

        Reuses ``verticals/<v>/stages.py``. A vertical whose module loads fine
        but does not define ``STAGE_ORDER`` gets the canonical 8-stage
        template (that vertical simply opted out of a custom stage list — not
        a failure). A vertical that fails to resolve/import PROPAGATES the
        error: this matches :meth:`divide`'s documented FAIL-HARD contract
        ("no silent fallback to the research default") and
        ``LifeSupervisor._resolve_vertical_once``'s own FAIL-HARD contract —
        silently substituting the canonical/paper stage list for a broken or
        unresolvable vertical would turn e.g. a kernelbench mission into the
        paper pipeline with no visible error.
        """
        from ..verticals._base import load_vertical

        order = getattr(
            load_vertical(vertical, project_root=self.project_root),
            "STAGE_ORDER", None,
        )
        if order:
            return list(order)
        from ..verticals.research.stages import CANONICAL_STAGE_ORDER

        return list(CANONICAL_STAGE_ORDER)

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
                )
            self._apply_vertical_decision_rendering(decision)
            return division
        vertical = decision.vertical
        stages = self.plan_stages(vertical)
        pipeline_state = self.project_root / "research" / "PIPELINE_STATE.json"
        with _restore_files_on_error([pipeline_state]):
            persist_vertical(
                self.project_root,
                vertical,
                domain=decision.domain or None,
                research_target_level=decision.research_target_level or None,
                workflow_mode=decision.workflow_mode,
                target_venue=decision.target_venue or None,
            )
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=old_vertical,
                new_vertical=vertical,
                force_replacement=force_stage_reset,
            )
        division = Division(
            task=task,
            vertical=vertical,
            domain=decision.domain,
            kind=self._kind_for(vertical),
            stages=stages,
            workflow_mode=decision.workflow_mode,
            execution_task=decision.execution_task,
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
        )
