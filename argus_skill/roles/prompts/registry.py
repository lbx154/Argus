"""Single resolver for role, vertical, stage, and scope prompt fragments."""

from __future__ import annotations

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
                workflow_mode="staged",
                requires_independent_review=False,
                search_altitude="",
                fragment_ids=(),
            )

        from ...verticals._base import (
            load_vertical,
            vertical_checklist_stage_order,
            vertical_completion_gate,
            vertical_requires_independent_review,
            vertical_role_banner,
            vertical_search_altitude,
            vertical_workflow_mode,
        )

        vertical_module = load_vertical(vertical, project_root=root)
        role_banner = vertical_role_banner(vertical_module, banner_role)
        stage_order = tuple(vertical_checklist_stage_order(vertical_module))
        stage = str(request.stage or "").strip()
        if not stage and request.checklist_mode is not ChecklistMode.NONE:
            from ...skills.stage_checklists import current_stage

            stage = current_stage(root or ".")

        checklist_mode = request.checklist_mode
        if checklist_mode is ChecklistMode.AUTO:
            checklist_mode = (
                ChecklistMode.FULL_PIPELINE
                if request.role is RoleName.REVIEWER
                and (scope == "final_submission" or stage == "submission")
                else ChecklistMode.STAGE
            )

        checklist = ""
        checklist_role = request.checklist_role or request.role
        if checklist_mode is ChecklistMode.STAGE:
            from ...skills.stage_checklists import format_stage_checklist

            checklist = format_stage_checklist(
                stage,
                role=checklist_role.value,
                project_root=root,
                scope=scope,
            )
        elif checklist_mode is ChecklistMode.FULL_PIPELINE:
            from ...skills.stage_checklists import format_full_pipeline_checklist

            checklist = format_full_pipeline_checklist(
                role=checklist_role.value,
                project_root=root,
            )

        search_altitude = (
            vertical_search_altitude(vertical_module, root)
            if request.include_search_altitude and root is not None
            else ""
        )
        fragment_ids: list[str] = []
        if role_banner.strip():
            fragment_ids.append(f"vertical:{vertical}:banner:{banner_role}")
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
            completion_gate=vertical_completion_gate(vertical_module),
            workflow_mode=vertical_workflow_mode(vertical_module),
            requires_independent_review=vertical_requires_independent_review(
                vertical_module
            ),
            search_altitude=search_altitude,
            fragment_ids=tuple(fragment_ids),
        )


PROMPT_CATALOG = RolePromptCatalog()


def resolve_role_prompt(request: RolePromptRequest) -> ResolvedRolePrompt:
    return PROMPT_CATALOG.resolve(request)


__all__ = ["PROMPT_CATALOG", "RolePromptCatalog", "resolve_role_prompt"]
