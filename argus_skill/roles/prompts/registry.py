"""Single resolver for role, vertical, stage, and scope prompt fragments."""

from __future__ import annotations

import os
from pathlib import Path

from . import engineer, manager, planner, reviewer
from .types import (
    ChecklistMode,
    ResolvedRolePrompt,
    RoleName,
    RolePromptRequest,
)

_OPERATIONS = {
    RoleName.MANAGER: manager.OPERATIONS,
    RoleName.PLANNER: planner.OPERATIONS,
    RoleName.ENGINEER: engineer.OPERATIONS,
    RoleName.REVIEWER: reviewer.OPERATIONS,
}


class RolePromptCatalog:
    """Resolve prompt hyperparameters without role-local vertical imports."""

    def operations_for(self, role: RoleName) -> frozenset[str]:
        try:
            return _OPERATIONS[role]
        except KeyError as exc:
            raise ValueError(f"unsupported prompt role: {role!r}") from exc

    def resolve(self, request: RolePromptRequest) -> ResolvedRolePrompt:
        operations = self.operations_for(request.role)
        if request.operation not in operations:
            raise ValueError(
                f"unsupported {request.role.value} prompt operation: "
                f"{request.operation!r}"
            )

        root = (
            Path(request.project_root).expanduser()
            if request.project_root is not None
            else None
        )
        vertical = str(request.vertical or "").strip()
        if not vertical and root is not None:
            from ...skills.vertical_select import resolve_vertical

            vertical = resolve_vertical(root)

        banner_role = str(request.banner_role or request.role.value).strip()
        scope = str(request.scope or "").strip().lower().replace("-", "_")
        if not vertical:
            return ResolvedRolePrompt(
                role=request.role,
                operation=request.operation,
                vertical="",
                banner_role=banner_role,
                stage=str(request.stage or "").strip(),
                scope=scope,
                role_banner="",
                stage_checklist="",
                stage_order=(),
                completion_gate="none",
                paper_mission=False,
                workflow_mode="staged",
                verification_stage_profiles={},
                requires_independent_review=False,
                search_altitude="",
                fragment_ids=(),
            )

        from ...verticals._base import load_vertical_contract

        contract = load_vertical_contract(vertical, project_root=root)
        vertical_banner = contract.banner(banner_role)
        domain = ""
        domain_banner = ""
        if root is not None and not str(request.vertical or "").strip():
            from ...skills.vertical_select import resolve_domain_if_decided

            domain = resolve_domain_if_decided(root) or ""
        stage_order = contract.stage_order
        stage = str(request.stage or "").strip()
        if not stage and request.checklist_mode is not ChecklistMode.NONE:
            from ...skills.stage_machine import current_stage

            stage = current_stage(root or ".")
        # Where the work is, as opposed to where its state is kept. Both the
        # vertical fragment and the altitude facts describe the work, so both
        # need the worktree; `root` is the session directory and contains no
        # paper/, which is how a manuscript-aware gate silently never fired.
        altitude_root = (
            Path(request.altitude_root).expanduser()
            if request.altitude_root is not None
            else root
        )
        vertical_fragment = contract.prompt_fragment(
            role=banner_role,
            operation=request.operation,
            stage=stage,
            scope=scope,
            project_root=altitude_root,
        )
        if vertical_fragment.strip():
            vertical_banner = "\n\n".join(
                part
                for part in (vertical_banner.strip(), vertical_fragment.strip())
                if part
            )
        # A controller-owned external gate is a stronger objective contract than
        # a generic vertical's optimization style. Keep the stage/checklist state,
        # but suppress a vertical banner that can otherwise redefine the task.
        if os.environ.get("ARGUS_SKILL_EXTERNAL_COMPLETION_GATE", "").strip():
            vertical_banner = ""
            vertical_fragment = ""
        role_banner = vertical_banner
        if domain:
            from ...domains import domain_role_banner, load_domain

            domain_banner = domain_role_banner(load_domain(domain), banner_role)
            role_banner = "\n\n".join(
                part for part in (role_banner.strip(), domain_banner.strip()) if part
            )

        checklist_mode = request.checklist_mode
        if checklist_mode is ChecklistMode.AUTO:
            checklist_mode = (
                ChecklistMode.FULL_PIPELINE
                if request.role is RoleName.REVIEWER
                and scope == "final_submission"
                else ChecklistMode.STAGE
            )

        checklist = ""
        checklist_role = request.checklist_role or request.role
        if checklist_mode is ChecklistMode.STAGE:
            from ...skills.stage_machine import format_stage_checklist

            checklist = format_stage_checklist(
                stage,
                role=checklist_role.value,
                project_root=root,
                scope=scope,
            )
        elif checklist_mode is ChecklistMode.FULL_PIPELINE:
            from ...skills.stage_machine import format_full_pipeline_checklist

            checklist = format_full_pipeline_checklist(
                role=checklist_role.value,
                project_root=root,
            )

        search_altitude = (
            contract.altitude(altitude_root)
            if request.include_search_altitude and altitude_root is not None
            else ""
        )
        fragment_ids: list[str] = []
        if vertical_banner.strip():
            fragment_ids.append(f"vertical:{vertical}:banner:{banner_role}")
        if vertical_fragment.strip():
            fragment_ids.append(
                f"vertical:{vertical}:prompt:{banner_role}:{request.operation}"
            )
        if domain_banner.strip():
            fragment_ids.append(f"domain:{domain}:banner:{banner_role}")
        if checklist.strip():
            checklist_kind = (
                "full_pipeline"
                if checklist_mode is ChecklistMode.FULL_PIPELINE
                else f"stage:{stage}"
            )
            fragment_ids.append(
                f"vertical:{vertical}:checklist:{checklist_role.value}:{checklist_kind}"
            )
        if search_altitude.strip():
            fragment_ids.append(f"vertical:{vertical}:search_altitude")

        return ResolvedRolePrompt(
            role=request.role,
            operation=request.operation,
            vertical=vertical,
            banner_role=banner_role,
            stage=stage,
            scope=scope,
            role_banner=role_banner,
            stage_checklist=checklist,
            stage_order=stage_order,
            completion_gate=contract.completion_gate,
            paper_mission=contract.paper_mission,
            workflow_mode=contract.workflow_mode,
            verification_stage_profiles=dict(
                contract.verification_stage_profiles or {}
            ),
            requires_independent_review=contract.requires_independent_review,
            search_altitude=search_altitude,
            fragment_ids=tuple(fragment_ids),
        )


PROMPT_CATALOG = RolePromptCatalog()


def resolve_role_prompt(request: RolePromptRequest) -> ResolvedRolePrompt:
    return PROMPT_CATALOG.resolve(request)


__all__ = ["PROMPT_CATALOG", "RolePromptCatalog", "resolve_role_prompt"]
