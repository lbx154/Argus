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
from pathlib import Path
from typing import Any

from ..skills import vertical_select
from ..skills.vertical_select import (
    persist_vertical,
)
from ._helpers import (
    _manager_backend_failure,
    _manager_model,
    gateway_run_exec,
    log,
)
from ._session_ops import _restore_files_on_error
from .domain_author import (
    VerticalDecision,
    VerticalDecisionError,
)

_log = logging.getLogger(__name__)


def _software_grounding_required(workflow_mode: str) -> bool:
    raw = os.environ.get("ARGUS_SKILL_SOFTWARE_REQUIRE_GROUNDING", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    # The grounded vertical decision already inspected the repository. Planner
    # and Engineer own any further task-specific inspection, so a second fresh
    # Manager inspection is opt-in rather than the staged-work default.
    return False


_CURRENT_OPERATOR_MARKER = "[CURRENT OPERATOR MESSAGE]"
_ROUTING_PROJECT_MARKERS = frozenset({
    ".git",
    "AGENTS.md",
    "Cargo.toml",
    "CMakeLists.txt",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
})


class _VerticalDecisionMixin:
    """Mixin: vertical selection, staging, and domain-commit methods."""

    project_root: Path

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
            "Use at most three targeted tool operations. Keep each result under "
            "80 lines and the combined returned evidence under 16,000 characters; "
            "do not repeat a listing/search already present in the task. "
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
        brief = (result.last_agent_message or "").strip()
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

    def decide_vertical(
        self,
        task: str,
        *,
        root_task_id: str | None = None,
        allow_route_contract_change: bool = False,
    ) -> VerticalDecision:
        """The route for ``task``: the one the Manager's ticket chose, as committed for this project.

        Work that never came through a Manager ticket has no route, and fails
        closed: Argus does not guess one.
        """
        return self._decide_vertical_once(
            task, root_task_id=root_task_id, allow_route_contract_change=allow_route_contract_change,
        )

    def _decide_vertical_once(
        self,
        task: str,
        *,
        root_task_id: str | None = None,
        allow_route_contract_change: bool = False,
        handed: dict[str, Any] | None = None,
    ) -> VerticalDecision:
        """Choose the vertical for ``task``.

        Every formal task is classified by the Manager. The Host supplies a
        bounded deterministic workspace snapshot; empty/new workspaces and clear
        built-in capabilities do not require ceremonial Agent tool use. Decisions
        that depend on an existing repository, project domain, or new capability
        require observed tool evidence and receive at most one automatic retry.

        FAIL-HARD when agent judgment is needed: no backend, or a model reply that
        is missing / not a valid choice, RAISES ``VerticalDecisionError``. There is
        NO keyword classifier and NO silent fallback to the research default.

        Persisted route values are immutable by default for durable recovery and
        supplemental work. The operator front door explicitly enables
        ``allow_route_contract_change`` for a new handoff outside an active
        campaign, where Manager is authorized to choose a different topology or
        success bar for the new requested outcome.
        """
        # Routing is intentionally isolated from the persistent Manager chat
        # session. Reusing prior conversation would violate the fast pass's
        # strict context bound and make cost depend on unrelated earlier turns.
        attempts_made = 0
        from ..domains import BUILTIN_DOMAINS
        from ..verticals._data_domain import list_all_data_domain_names
        from .domain_author import RouteContext

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
        from ..verticals._base import load_vertical_contract

        research_target_verticals = tuple(
            name
            for name in vertical_select.available_verticals()
            if load_vertical_contract(name, project_root=self.project_root, scoped=False).research_target_levels
        )
        known_verticals = list(vertical_select.available_verticals())
        persisted_vertical = (
            vertical_select.resolve_vertical_if_decided(self.project_root) or ""
        )
        persisted_workflow_mode = (
            vertical_select.resolve_workflow_mode(self.project_root)
            if persisted_vertical
            else ""
        )
        persisted_domain = (
            vertical_select.resolve_domain_if_decided(self.project_root) or ""
        )
        from ..core.research_contract import (
            resolve_research_direction_mode,
            resolve_research_target_level,
        )

        persisted_research_target_level = (
            resolve_research_target_level(self.project_root) or ""
        )
        persisted_research_direction_mode = (
            resolve_research_direction_mode(self.project_root) or ""
        )
        from ..core.pipeline_state import read_pipeline_state

        prior_state = read_pipeline_state(self.project_root)

        def finalize(decision: VerticalDecision) -> VerticalDecision:
            if decision.choice == "existing":
                from ..core.vertical_contract import VerticalContractError
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
                    scoped=False,
                )
                if contract.workflow_profiles:
                    if persisted_vertical == decision.vertical and not allow_route_contract_change:
                        try:
                            active = load_vertical_contract(decision.vertical, self.project_root)
                            proposed = (
                                contract.for_profile(
                                    decision.workflow_profile or prior_state.get("workflow_profile", ""),
                                    requested_stages=decision.workflow_requested_stages,
                                )
                                if decision.workflow_requested_stages else None
                            )
                        except VerticalContractError as exc:
                            raise VerticalDecisionError(
                                f"invalid active workflow: {exc}",
                                contract_field="workflow_profile",
                            ) from exc
                        previous = prior_state.get("workflow_profile", "")
                        if decision.workflow_profile and decision.workflow_profile != previous:
                            raise VerticalDecisionError("workflow profile cannot change during an active task")
                        if proposed is not None:
                            if proposed.workflow_requested_stages != active.workflow_requested_stages:
                                raise VerticalDecisionError("workflow requested stages cannot change during an active task")
                        decision.workflow_profile = previous
                        decision.workflow_requested_stages = active.workflow_requested_stages
                    elif not decision.workflow_profile:
                        raise VerticalDecisionError(
                            "choose a workflow_profile from the selected vertical's menu",
                            contract_field="workflow_profile",
                        )
                    if decision.workflow_profile:
                        try:
                            selected = contract.for_profile(
                                decision.workflow_profile,
                                requested_stages=decision.workflow_requested_stages,
                            )
                        except VerticalContractError as exc:
                            raise VerticalDecisionError(
                                f"invalid workflow_profile: {exc}",
                                contract_field="workflow_profile",
                            ) from exc
                        decision.workflow_requested_stages = selected.workflow_requested_stages
                        if decision.workflow_mode != "staged":
                            raise VerticalDecisionError(
                                "workflow profiles require WORKFLOW_MODE=staged",
                                contract_field="workflow_mode",
                            )
                        decision.start_stage = ""
                elif decision.workflow_profile or decision.workflow_requested_stages:
                    # A profile only selects among a vertical's declared
                    # profiles. When the chosen vertical declares none, the
                    # supplied profile/stage request has nothing to bind to and
                    # cannot change the route; drop it visibly instead of
                    # failing the whole route (which killed fresh daemons).
                    dropped = decision.workflow_profile or "custom"
                    dropped_stages = list(decision.workflow_requested_stages)
                    log.warning(
                        "Manager route chose vertical %s, which provides no "
                        "workflow profiles; ignoring workflow_profile=%r "
                        "requested_stages=%r",
                        decision.vertical,
                        decision.workflow_profile,
                        dropped_stages,
                    )
                    note = (
                        f"[host] ignored workflow_profile={dropped!r}"
                        + (f" requested_stages={dropped_stages!r}" if dropped_stages else "")
                        + f": vertical {decision.vertical!r} provides no workflow profiles"
                    )
                    decision.adaptation_reason = (
                        f"{decision.adaptation_reason} {note}".strip()
                        if decision.adaptation_reason
                        else note
                    )
                    decision.workflow_profile = ""
                    decision.workflow_requested_stages = ()
                if (
                    handed is None
                    and contract.ground_before_handoff
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
                    "instead of producing a standalone handoff",
                    phase="contract",
                    contract_field="execution_task",
                    attempts=attempts_made,
                    task=task,
                )
            return decision

        context = RouteContext(
            known_verticals=tuple(known_verticals),
            known_domains=tuple(BUILTIN_DOMAINS),
            existing_data_domains=tuple(all_domain_names),
            research_target_verticals=research_target_verticals,
            default_execution_task="" if contextual_task else task.strip(),
            persisted_vertical=persisted_vertical,
            persisted_workflow_mode=persisted_workflow_mode,
            persisted_domain=persisted_domain,
            persisted_research_target_level=persisted_research_target_level,
            persisted_research_direction_mode=persisted_research_direction_mode,
            allow_persisted_change=allow_route_contract_change,
            project_root=self.project_root,
        )
        if handed is None:
            if not persisted_vertical:
                # A project from before tickets, or work nobody handed over:
                # held until the operator's next message brings the Manager's ticket.
                raise vertical_select.AwaitingManagerTicketError(
                    "Waiting for the Manager's ticket: no route has been chosen for this work yet."
                )
            # Work the Manager already routed keeps the route its ticket chose.
            handed = {
                "vertical": persisted_vertical, "workflow_mode": persisted_workflow_mode,
                "research_target_level": persisted_research_target_level,
                "research_direction_mode": persisted_research_direction_mode,
                "execution_task": task, "summary": "the route the Manager's ticket chose", "assumptions": [],
            }
        # The Manager chose the route in its ticket: checked here, no routing call.
        known = {name.strip().lower() for name in (*known_verticals, *all_domain_names)}
        choice = "existing" if str(handed["vertical"]).strip().lower() in known | {"direct"} else "new"
        try:
            return finalize(context.decide({**handed, "choice": choice}))
        except ValueError as exc:
            raise VerticalDecisionError(str(exc), phase="contract", task=task) from exc

    def adopt_route(
        self,
        payload: dict[str, Any],
        *,
        root_task_id: str | None = None,
        allow_route_contract_change: bool = False,
    ) -> VerticalDecision:
        """The route the Manager chose in its ``manager`` ticket, checked like a routing decision.

        A refusal is a ``ValueError`` the Manager reads and corrects in the same turn.
        """
        try:
            return self._decide_vertical_once(
                payload["execution_task"],
                root_task_id=root_task_id,
                allow_route_contract_change=allow_route_contract_change,
                handed=payload,
            )
        except VerticalDecisionError as exc:
            raise ValueError(exc.cause) from exc

    # ---- split into the vertical's Stage template ----
    def plan_stages(self, vertical: str) -> list[str]:
        """Return the selected vertical's own ordered stages.

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
                    task=task, vertical=proposal.name,
                    stages=list(proposal.stages),
                    domain="",
                    workflow_mode=decision.workflow_mode,
                    start_stage=decision.start_stage,
                    execution_task=decision.execution_task,
                    require_independent_review=decision.require_independent_review,
                    proposed_domain=proposal, pending_confirmation=True,
                )
                return division
            division = self._commit_domain_locked(
                task,
                proposal,
                _old_vertical=old_vertical,
                execution_task=decision.execution_task,
                workflow_mode=decision.workflow_mode,
                start_stage=decision.start_stage,
            )
            division.require_independent_review = (
                decision.require_independent_review
            )
            if force_stage_reset:
                vertical_select.reset_stage_for_new_intent(
                    self.project_root,
                    old_vertical=old_vertical,
                    new_vertical=division.vertical,
                    force_replacement=True,
                    evidence_root=self.execution_workdir,
                )
            return division
        vertical = decision.vertical
        from ..verticals._data_domain import (
            load_data_domain,
            materialize_learned_data_domain,
        )

        materialize_learned_data_domain(
            self.learned_vertical_root,
            self.project_root,
            vertical,
        )
        from ..core.pipeline_state import (
            legacy_pipeline_state_path,
            primary_pipeline_state_path,
            read_pipeline_state,
        )

        pipeline_states = [
            primary_pipeline_state_path(self.project_root),
            legacy_pipeline_state_path(self.project_root),
        ]
        with _restore_files_on_error(pipeline_states):
            from ..verticals._base import load_vertical_contract

            contract = load_vertical_contract(vertical, self.project_root, scoped=False)
            if (
                contract.workflow_profiles and not decision.workflow_profile
                and old_vertical != vertical
            ):
                raise VerticalDecisionError(
                    "new task requires a workflow_profile", contract_field="workflow_profile",
                )
            direction_mode = decision.research_direction_mode or None
            if (
                vertical == old_vertical == "research"
                and decision.workflow_mode == "direct"
                and not force_stage_reset
            ):
                # Editing an existing artifact does not re-select the research
                # direction merely because its now-known mechanism is locked.
                direction_mode = (
                    read_pipeline_state(self.project_root).get("research_direction_mode")
                    or direction_mode
                )
            persist_vertical(
                self.project_root,
                vertical,
                domain=decision.domain or None,
                research_target_level=decision.research_target_level or None,
                research_direction_mode=direction_mode,
                workflow_mode=decision.workflow_mode,
                start_stage=decision.start_stage,
                target_venue=decision.target_venue or None,
                operator_decides_stages=decision.operator_decides_stages,
                allow_research_direction_change=force_stage_reset,
                workflow_profile=decision.workflow_profile or None,
                workflow_requested_stages=decision.workflow_requested_stages or None,
                allow_workflow_profile_change=force_stage_reset,
            )
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=old_vertical,
                new_vertical=vertical,
                force_replacement=force_stage_reset,
                evidence_root=self.execution_workdir,
                start_stage=(decision.start_stage if decision.workflow_mode == "direct" else ""),
            )
            self._adopt_operator_objective(vertical, decision, task)
            stages = self.plan_stages(vertical)
            selected_contract = load_vertical_contract(vertical, self.project_root)
        division = Division(
            task=task,
            vertical=vertical,
            domain=decision.domain,
            stages=stages,
            workflow_profile=selected_contract.workflow_profile,
            workflow_summary=selected_contract.workflow_summary(),
            workflow_mode=decision.workflow_mode,
            start_stage=decision.start_stage,
            execution_task=decision.execution_task,
            require_independent_review=decision.require_independent_review,
            # Anything that is neither built in nor an installed plugin vertical
            # is a Manager-authored data domain; ``VERTICALS`` alone would
            # misclassify a community vertical (``quant``, ``medical``, ...).
            learned_vertical_status=(
                getattr(
                    load_data_domain(vertical, self.project_root),
                    "status",
                    "",
                )
                if vertical not in vertical_select.available_verticals()
                else ""
            ),
        )
        return division

    def _adopt_operator_objective(
        self, vertical: str, decision: VerticalDecision, task: str
    ) -> None:
        """Hand the chosen vertical the operator's request, if it wants one.

        A vertical whose completion rule depends on a choice core cannot make
        (``math``: prove one named goal, or explore a direction?) gets exactly
        one chance to record that choice from the operator's own words — here,
        where the request text and the freshly persisted project state are both
        in hand. Placed after ``reset_stage_for_new_intent`` so a rollback that
        rewrites the pipeline state cannot erase what was just adopted.

        ``execution_task`` before ``task``: it is the statement the pipeline
        actually pursues, and a vertical that turns it into a completion target
        must be measured against the same text the Planner and Engineer were
        given. ``task`` is the fallback for the paths that never produced one.

        Adopted into the execution workdir as well as the project root, when
        those differ. Not belt-and-braces: ``_ensure_stage_completion`` invokes
        the vertical validator with ``evidence_root or project_root``, so in the
        split layout the daemon actually runs — pipeline state under
        ``~/.argus-skill/projects/<sid>``, artifacts in the operator's repo —
        the validator reads the *workdir* copy, and adopting only into the
        project root would leave the gate exactly as unsatisfiable as before.
        The two copies cannot drift: adoption never overwrites a resolved
        objective, and both are written from this one request string.

        Fail-open, like ``reset_stage_for_new_intent`` next to it. Raising here
        would sink a correctly persisted vertical decision over an optional
        convenience, and a learned data domain has no vertical module at all —
        a lookup failure, not a defect. The cost of failing open is that the
        vertical falls back to refusing completion with an explicit
        unresolved-objective message, which is the state this hook exists to
        improve on, not a silent wrong answer.
        """
        from ..verticals._base import load_vertical_contract

        request = decision.execution_task.strip() or task
        roots = [self.project_root]
        workdir = getattr(self, "execution_workdir", None)
        if workdir is not None and Path(workdir) != Path(self.project_root):
            roots.append(Path(workdir))
        for root in roots:
            try:
                load_vertical_contract(vertical, project_root=self.project_root).adopt_operator_objective(
                    project_root=Path(root),
                    request=request,
                )
            except Exception:  # noqa: BLE001 — never break division on a hook
                _log.debug(
                    "objective adoption skipped for %r at %s",
                    vertical,
                    root,
                    exc_info=True,
                )

    def commit_domain(
        self,
        task: str,
        proposal: Any,
        *,
        _old_vertical: str | None = None,
        execution_task: str = "",
        workflow_mode: str = "staged",
        start_stage: str = "",
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
                start_stage=start_stage,
            )

    def _commit_domain_locked(
        self,
        task: str,
        proposal: Any,
        *,
        _old_vertical: str | None,
        execution_task: str,
        workflow_mode: str,
        start_stage: str,
    ) -> Any:
        from ..verticals._data_domain import write_data_domain
        from ._core import Division

        if _old_vertical is None:
            _old_vertical = vertical_select._persisted_vertical(self.project_root)

        from ..core.pipeline_state import (
            legacy_pipeline_state_path,
            primary_pipeline_state_path,
        )

        pipeline_states = [
            primary_pipeline_state_path(self.project_root),
            legacy_pipeline_state_path(self.project_root),
        ]
        domain_path = (
            self.project_root
            / "research"
            / "DOMAINS"
            / f"{proposal.name}.json"
        )
        with _restore_files_on_error([*pipeline_states, domain_path]):
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
                **({"role_banner": proposal.capability_brief} if getattr(proposal, "capability_brief", "") else {}),
            )
            persist_vertical(
                self.project_root,
                proposal.name,
                workflow_mode=workflow_mode,
                start_stage=start_stage,
            )
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=_old_vertical,
                new_vertical=proposal.name,
                evidence_root=self.execution_workdir,
            )
        return Division(
            task=task, vertical=proposal.name,
            stages=list(proposal.stages), proposed_domain=proposal,
            execution_task=(
                execution_task
                or str(getattr(proposal, "execution_task", "") or "")
            ),
            workflow_mode=workflow_mode,
            start_stage=start_stage,
            pending_confirmation=False,
            learned_vertical_status="candidate",
        )
