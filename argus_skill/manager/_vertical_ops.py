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

from ..core.role_decision import latest_role_decision
from ..skills import vertical_select
from ..skills.vertical_select import (
    persist_vertical,
)
from ._helpers import (
    _DEFAULT_FAST_ROUTE_MAX_PROMPT_CHARS,
    _DEFAULT_FAST_ROUTE_MAX_TASK_CHARS,
    _DEFAULT_GROUNDED_ROUTE_MAX_PROMPT_CHARS,
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
from .domain_author import (
    ManagerClassificationContractError,
    VerticalDecision,
    VerticalDecisionError,
)

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
    # The grounded vertical decision already inspected the repository. Planner
    # and Engineer own any further task-specific inspection, so a second fresh
    # Manager inspection is opt-in rather than the staged-work default.
    return False


_CURRENT_OPERATOR_MARKER = "[CURRENT OPERATOR MESSAGE]"
_ROUTING_RUNTIME_ENTRIES = frozenset({".argus", ".autors"})
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


def _routing_workspace_snapshot(root: Path | str) -> dict[str, Any]:
    """Return bounded deterministic routing evidence without model tool use."""
    path = Path(root).expanduser().resolve()
    try:
        entries = sorted(
            (
                child.name + ("/" if child.is_dir() else "")
                for child in path.iterdir()
                if child.name not in _ROUTING_RUNTIME_ENTRIES
            ),
            key=str.casefold,
        )[:40]
    except OSError:
        return {
            "root": str(path),
            "accessible": False,
            "workspace_empty": False,
            "entries": [],
            "project_markers": [],
        }
    marker_names = {
        entry.rstrip("/")
        for entry in entries
        if entry.rstrip("/") in _ROUTING_PROJECT_MARKERS
    }
    return {
        "root": str(path),
        "accessible": True,
        "workspace_empty": not entries,
        "entries": entries,
        "project_markers": sorted(marker_names),
    }


def _render_routing_workspace_snapshot(snapshot: dict[str, Any]) -> str:
    entries = list(snapshot.get("entries") or [])
    markers = list(snapshot.get("project_markers") or [])
    return (
        "\n\n## Host workspace snapshot (authoritative bounded routing evidence)\n"
        f"manager_tool_root={snapshot.get('root') or ''}\n"
        "container_path_mapping=/app -> manager_tool_root (Manager tools must use "
        "the Host path; preserve /app only inside commands handed to Engineer)\n"
        f"accessible={str(bool(snapshot.get('accessible'))).lower()}\n"
        f"workspace_empty={str(bool(snapshot.get('workspace_empty'))).lower()}\n"
        f"project_markers={'; '.join(str(value) for value in markers) or 'none'}\n"
        f"top_level_entries={'; '.join(str(value) for value in entries) or 'none'}\n"
        "An empty snapshot is sufficient evidence that no repository-specific "
        "capability can be reused. Do not call a tool merely to repeat it."
    )


def _render_active_route_contract(
    *,
    vertical: str,
    workflow_mode: str,
    domain: str,
    research_target_level: str,
    research_direction_mode: str,
    allow_change: bool,
) -> str:
    """Tell the model about the state that the parser will reconcile against."""
    if not vertical:
        return ""
    if allow_change:
        policy = (
            "This is a new operator handoff. You may revise these values only when "
            "the current requested outcome requires it; otherwise omit them or "
            "preserve them."
        )
    else:
        policy = (
            "This is supplemental work inside an active campaign. If you select "
            "the same vertical, preserve these values exactly; omit unchanged "
            "fields rather than guessing replacements."
        )
    return (
        "\n\n## Active persisted route contract (authoritative)\n"
        f"vertical={vertical}\n"
        f"workflow_mode={workflow_mode or 'unset'}\n"
        f"domain={domain or 'none'}\n"
        f"research_target_level={research_target_level or 'none'}\n"
        f"research_direction_mode={research_direction_mode or 'none'}\n"
        f"policy={policy}\n"
        "Legal workflow_mode values: direct, staged. Legal research target values: "
        "exploratory, publishable, doctoral. Legal research_direction_mode values: "
        "broad, locked. Do not use a target level as a direction mode."
    )


def _task_excerpt(task: str, *, limit: int = 180) -> str:
    compact = " ".join(str(task or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _decision_requires_agent_grounding(
    decision: VerticalDecision,
    *,
    snapshot: dict[str, Any],
    builtin_verticals: set[str],
    project_domains: set[str],
) -> bool:
    """Whether this decision needs evidence beyond the Host snapshot."""
    if not bool(snapshot.get("accessible")):
        return True
    if decision.choice != "existing":
        return True
    if decision.vertical in project_domains or decision.vertical not in builtin_verticals:
        return True
    if decision.adapted_stages:
        return True
    return False


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
        process_decision = latest_role_decision(result, "manager")
        target_level = parse_research_target_level(
            process_decision
            if process_decision is not None
            else extract_answer(result),
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
        allow_route_contract_change: bool = False,
    ) -> VerticalDecision:
        """Classify one task and maintain the resolved model's durable streak.

        Only the two typed capability-contract failures are recorded.  A full
        successful classification clears this model's streak; provider and
        other operational failures leave it unchanged and retain their
        existing exception/message behavior.
        """
        from .classification_contract import (
            record_contract_failure,
            reset_contract_failures,
        )

        resolved_model_id = _manager_model() or "<backend default>"
        try:
            decision = self._decide_vertical_once(
                task,
                root_task_id=root_task_id,
                allow_route_contract_change=allow_route_contract_change,
                resolved_model_id=resolved_model_id,
            )
        except ManagerClassificationContractError as exc:
            try:
                with self.pipeline_lock():
                    count = record_contract_failure(
                        self.project_root,
                        model_id=resolved_model_id,
                        clause=exc.clause,
                    )
            except Exception:  # noqa: BLE001 - diagnostics must not mask fail-closed routing
                log.exception("could not persist Manager contract-failure streak")
                count = 0
            exc.attach_streak(
                model_id=resolved_model_id,
                consecutive_count=count,
            )
            raise
        try:
            with self.pipeline_lock():
                reset_contract_failures(
                    self.project_root,
                    model_id=resolved_model_id,
                )
        except Exception:  # noqa: BLE001 - a diagnostic reset must not reject a valid route
            log.exception("could not reset Manager contract-failure streak")
        return decision

    def _decide_vertical_once(
        self,
        task: str,
        *,
        root_task_id: str | None = None,
        allow_route_contract_change: bool = False,
        resolved_model_id: str | None = None,
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
        backend = self.runner
        if backend is None:
            raise VerticalDecisionError(
                "cannot decide the vertical: the Manager has no backend/runner"
            )
        manager_model = resolved_model_id or _manager_model()
        from ..core.models import RunnerOptions
        from ..domains import BUILTIN_DOMAINS, DOMAIN_PURPOSES
        from ..roles.prompts.manager import (
            build_fast_vertical_decision_prompt,
            build_vertical_decision_prompt,
        )
        from ..verticals._data_domain import (
            list_all_data_domain_names,
            list_selectable_data_domain_summaries,
        )
        from .domain_author import (
            parse_fast_vertical_decision,
            parse_vertical_decision,
        )
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
            or ""
        ).strip().lower()
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
        active_route_contract = _render_active_route_contract(
            vertical=persisted_vertical,
            workflow_mode=persisted_workflow_mode,
            domain=persisted_domain,
            research_target_level=persisted_research_target_level,
            research_direction_mode=persisted_research_direction_mode,
            allow_change=allow_route_contract_change,
        )

        def finalize(decision: VerticalDecision) -> VerticalDecision:
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

        workspace_snapshot = _routing_workspace_snapshot(self.execution_workdir)
        if (
            not contextual_task
            and _manager_fast_route_enabled()
            and bool(workspace_snapshot.get("workspace_empty"))
            and not existing
            and len(task.strip())
            <= _manager_route_positive_int(
                "ARGUS_SKILL_MANAGER_FAST_ROUTE_MAX_TASK_CHARS",
                _DEFAULT_FAST_ROUTE_MAX_TASK_CHARS,
            )
        ):
            fast_prompt = build_fast_vertical_decision_prompt(
                task,
                verticals_with_purpose=vertical_select.available_vertical_purposes(),
                domains_with_purpose=DOMAIN_PURPOSES,
                existing_data_domains=existing,
                research_target_verticals=research_target_verticals,
            ) + active_route_contract
            if len(fast_prompt) <= _manager_route_positive_int(
                "ARGUS_SKILL_MANAGER_FAST_ROUTE_MAX_PROMPT_CHARS",
                _DEFAULT_FAST_ROUTE_MAX_PROMPT_CHARS,
            ):
                with self._task_usage_scope(root_task_id):
                    fast_result = gateway_run_exec(
                        backend,
                        prompt=fast_prompt,
                        options=RunnerOptions(
                            model=manager_model,
                            reasoning_effort=_manager_vertical_reasoning_effort(),
                            working_dir=str(self.execution_workdir),
                            sandbox_mode="read-only",
                            force_safe_mode=True,
                            skip_git_repo_check=True,
                        ),
                        run_label="manager-classify-fast",
                    )
                failed, detail = _manager_backend_failure(fast_result)
                if failed:
                    raise VerticalDecisionError(
                        "Manager fast-route backend failed"
                        + (f": {detail}" if detail else "")
                    )
                fast_payload = latest_role_decision(fast_result, "manager")
                fast_route = parse_fast_vertical_decision(
                    fast_payload
                    if fast_payload is not None
                    else extract_answer(fast_result),
                    known_verticals=known_verticals,
                    known_domains=list(BUILTIN_DOMAINS),
                    existing_data_domains=all_domain_names,
                    research_target_verticals=research_target_verticals,
                    persisted_vertical=persisted_vertical,
                    persisted_workflow_mode=persisted_workflow_mode,
                    persisted_domain=persisted_domain,
                    persisted_research_target_level=(
                        persisted_research_target_level
                    ),
                    persisted_research_direction_mode=(
                        persisted_research_direction_mode
                    ),
                    allow_persisted_change=allow_route_contract_change,
                )
                if (
                    fast_route is not None
                    and not fast_route.needs_grounding
                    and fast_route.confidence >= _manager_fast_route_min_confidence()
                ):
                    return finalize(VerticalDecision(
                        choice="existing",
                        vertical=fast_route.vertical,
                        domain=fast_route.domain,
                        workflow_mode=fast_route.workflow_mode,
                        adaptation_reason=fast_route.rationale,
                        execution_task=task.strip(),
                        research_target_level=fast_route.research_target_level,
                        research_direction_mode=(
                            fast_route.research_direction_mode
                        ),
                        target_venue=fast_route.target_venue,
                    ))

        prompt = build_vertical_decision_prompt(
            task,
            verticals_with_purpose=vertical_select.available_vertical_purposes(),
            domains_with_purpose=DOMAIN_PURPOSES,
            existing_data_domains=existing,
            existing_data_domain_summaries=existing_summaries,
            research_target_verticals=research_target_verticals,
        ) + active_route_contract + _render_routing_workspace_snapshot(
            workspace_snapshot
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
        options = RunnerOptions(
            model=manager_model,
            reasoning_effort=_manager_vertical_reasoning_effort(),
            working_dir=str(self.execution_workdir),
            sandbox_mode="read-only",
            force_safe_mode=True,
            skip_git_repo_check=True,
            extra_args=grounded_extra_args,
        )

        def invoke_grounded_route(
            route_prompt: str,
            *,
            run_label: str,
        ) -> tuple[Any, VerticalDecision]:
            with self._task_usage_scope(root_task_id):
                route_result = gateway_run_exec(
                    backend,
                    prompt=route_prompt,
                    options=options,
                    run_label=run_label,
                )
            failed, detail = _manager_backend_failure(route_result)
            if failed:
                raise VerticalDecisionError(
                    "Manager grounded-route backend failed"
                    + (f": {detail}" if detail else "")
                )
            route_payload = latest_role_decision(route_result, "manager")
            route_decision = parse_vertical_decision(
                route_payload
                if route_payload is not None
                else extract_answer(route_result),
                known_verticals=known_verticals,
                known_domains=list(BUILTIN_DOMAINS),
                existing_data_domains=all_domain_names,
                research_target_verticals=research_target_verticals,
                default_execution_task="" if contextual_task else task.strip(),
                persisted_vertical=persisted_vertical,
                persisted_workflow_mode=persisted_workflow_mode,
                persisted_domain=persisted_domain,
                persisted_research_target_level=(
                    persisted_research_target_level
                ),
                persisted_research_direction_mode=(
                    persisted_research_direction_mode
                ),
                allow_persisted_change=allow_route_contract_change,
            )
            if route_decision is None:
                from .classification_contract import STRUCTURED_DECISION_CLAUSE

                raise ManagerClassificationContractError(
                    "Manager could not decide a vertical: the model reply was "
                    "missing, not a valid existing/new choice, or violated the "
                    "persisted route contract. "
                    f"Task excerpt: {_task_excerpt(task)!r}",
                    clause=STRUCTURED_DECISION_CLAUSE,
                )
            return route_result, route_decision

        try:
            result, decision = invoke_grounded_route(
                prompt,
                run_label="manager-classify-grounded",
            )
        except VerticalDecisionError as exc:
            if contextual_task and "could not decide a vertical" in str(exc):
                result, decision = invoke_grounded_route(
                    prompt
                    + "\n\n## Context handoff correction\n"
                    "The prior decision event was incomplete or invalid. Record one "
                    "complete Manager decision event again. Because the Task contains bounded "
                    "conversation context, EXECUTION_TASK is required: rewrite only "
                    "the current intended work as a standalone handoff, preserving "
                    "every explicit constraint and excluding the context markers. "
                    "Use only direct/staged for workflow_mode, "
                    "exploratory/publishable/doctoral for research_target_level, "
                    "and broad/locked for research_direction_mode. Follow the Active "
                    "persisted route contract policy above.",
                    run_label="manager-classify-context-retry",
                )
            elif "not a valid existing/new choice" in str(exc):
                result, decision = invoke_grounded_route(
                    prompt
                    + "\n\n## Decision-field correction\n"
                    "The prior decision event used an invalid capability identity. "
                    "Record one complete Manager decision event again. If choosing an "
                    "existing project domain, put its exact slug in `vertical` and "
                    "leave `domain` empty. `domain` may only name an optional research "
                    "domain listed above. If the chosen vertical supports research "
                    "targets, include `RESEARCH_TARGET_LEVEL` using exactly one of "
                    "exploratory, publishable, or doctoral; do not omit it unless the "
                    "active persisted route contract already supplies it.",
                    run_label="manager-classify-field-retry",
                )
            elif "repeated tool call detected" not in str(exc):
                raise
            else:
                result, decision = invoke_grounded_route(
                    prompt
                    + "\n\n## Tool-loop correction\n"
                    "The prior turn repeated one failed tool call. Do not repeat it. "
                    "Use manager_tool_root for any further repository inspection, or "
                    "return the routing decision now if the Host snapshot and Task are "
                    "already sufficient.",
                    run_label="manager-classify-tool-loop-retry",
                )
        tool_activity = bool(getattr(result, "tool_activity_observed", False))
        grounding_required = _decision_requires_agent_grounding(
            decision,
            snapshot=workspace_snapshot,
            builtin_verticals=set(known_verticals),
            project_domains=set(all_domain_names),
        )
        if grounding_required and not tool_activity:
            correction = (
                "\n\n## Required grounding retry\n"
                "The prior structured decision selected a repository-sensitive "
                "or project-local capability without Agent tool evidence. Use one "
                "targeted read-only repository tool operation, then record the "
                "complete Manager decision event. Do not broaden the search."
            )
            retry_prompt = prompt + correction
            if len(retry_prompt) > grounded_prompt_limit:
                raise VerticalDecisionError(
                    "Manager grounded-route retry exceeds configured context cap"
                )
            result, decision = invoke_grounded_route(
                retry_prompt,
                run_label="manager-classify-grounded-retry",
            )
            if not bool(getattr(result, "tool_activity_observed", False)):
                from .classification_contract import REPOSITORY_TOOL_CLAUSE

                raise ManagerClassificationContractError(
                    "Manager grounded vertical decision did not inspect repository "
                    "tools after one automatic retry",
                    clause=REPOSITORY_TOOL_CLAUSE,
                )
        return finalize(decision)

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
                    require_independent_review=decision.require_independent_review,
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
        from ..core.pipeline_state import (
            legacy_pipeline_state_path,
            primary_pipeline_state_path,
        )

        pipeline_states = [
            primary_pipeline_state_path(self.project_root),
            legacy_pipeline_state_path(self.project_root),
        ]
        domain_path = (
            self.project_root / "research" / "DOMAINS" / f"{vertical}.json"
        )
        index_path = self.project_root / "research" / "DOMAINS" / "INDEX.json"
        adapted = bool(
            decision.adapted_stages
            and load_data_domain(vertical, self.project_root) is not None
        )
        restore_paths = list(pipeline_states)
        if adapted:
            restore_paths.extend((domain_path, index_path))
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
                research_direction_mode=decision.research_direction_mode or None,
                workflow_mode=decision.workflow_mode,
                target_venue=decision.target_venue or None,
            )
            vertical_select.reset_stage_for_new_intent(
                self.project_root,
                old_vertical=old_vertical,
                new_vertical=vertical,
                force_replacement=force_stage_reset or adapted,
                evidence_root=self.execution_workdir,
            )
            self._adopt_operator_objective(vertical, decision, task)
        division = Division(
            task=task,
            vertical=vertical,
            domain=decision.domain,
            kind=self._kind_for(vertical),
            stages=stages,
            workflow_mode=decision.workflow_mode,
            execution_task=decision.execution_task,
            require_independent_review=decision.require_independent_review,
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
        from ..verticals._base import (
            load_vertical,
            vertical_adopt_operator_objective,
        )

        request = decision.execution_task.strip() or task
        roots = [self.project_root]
        workdir = getattr(self, "execution_workdir", None)
        if workdir is not None and Path(workdir) != Path(self.project_root):
            roots.append(Path(workdir))
        for root in roots:
            try:
                vertical_adopt_operator_objective(
                    load_vertical(vertical, project_root=self.project_root),
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
