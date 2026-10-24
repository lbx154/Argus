from __future__ import annotations

import pytest

from argus.roles.prompts import (
    ChecklistMode,
    RoleName,
    RolePromptRequest,
    resolve_role_prompt,
)
from argus.roles.prompts.engineer import (
    mission_request,
)
from argus.roles.prompts.planner import (
    continuous_request,
    preview_request,
)
from argus.skills.vertical_select import persist_vertical
from argus.verticals._base import load_vertical_contract


def test_engineer_banner_resolves_through_role_catalog(optimize_vertical, tmp_path) -> None:
    persist_vertical(tmp_path, optimize_vertical)
    contract = load_vertical_contract(optimize_vertical, project_root=tmp_path)

    engineer = resolve_role_prompt(mission_request(tmp_path))

    assert engineer.vertical == optimize_vertical
    assert engineer.role_banner == contract.banner("engineer")
    assert engineer.stage_checklist == ""
    assert engineer.fragment_ids == (
        "vertical:optimize_lab:banner:engineer",
    )


def test_unknown_role_operation_fails_loudly(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported reviewer prompt operation"):
        resolve_role_prompt(
            RolePromptRequest(
                role=RoleName.REVIEWER,
                operation="typo",
                project_root=tmp_path,
                checklist_mode=ChecklistMode.NONE,
            )
        )


def test_planner_preview_uses_same_vertical_banner(optimize_vertical, tmp_path) -> None:
    persist_vertical(tmp_path, optimize_vertical)

    preview = resolve_role_prompt(preview_request(tmp_path))
    continuous = resolve_role_prompt(continuous_request(tmp_path))

    assert preview.role_banner == continuous.role_banner
