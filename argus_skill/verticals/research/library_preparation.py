"""Research-only Skill preparation for the four-stage workflow."""
from __future__ import annotations

from ...core.vertical_contract import VerticalLibraryContext

STAGE_PLAYBOOK_PATHS: dict[str, str] = {
    stage: f"research-{stage}-playbook.md"
    for stage in ("idea", "experiment", "paper", "review")
}


def prepare_skill_libraries(context: VerticalLibraryContext) -> None:
    """Prepare only the active stage's research Skills and internal idea team."""
    playbook = STAGE_PLAYBOOK_PATHS.get(context.stage)
    if playbook:
        context.required_skill_paths.append(playbook)

    if not context.paper_mission:
        return
    from ...skills.stage_machine import migrate_legacy_research_stage

    migrate_legacy_research_stage(context.state_root)
    from .idea_portfolio import (
        DEFAULT_PORTFOLIO_SIZE,
        SELECTION_POLICY,
        ensure_idea_portfolio,
        idea_portfolio_selection,
        migrate_legacy_idea_selection,
        portfolio_required,
    )

    migrate_legacy_idea_selection(
        context.workdir,
        state_root=context.state_root,
    )
    if context.stage != "idea":
        return

    if context.workflow_mode == "direct" or not portfolio_required(context.state_root):
        return
    if context.team_task_id:
        context.emit({
            "type": "idea.portfolio.nested_skipped",
            "team_task_id": context.team_task_id,
            "text": "team worker reused the parent portfolio without recursive fanout",
        })
        return

    team_root = ensure_idea_portfolio(
        context.workdir,
        direction=context.direction,
        state_root=context.state_root,
    )
    selection = idea_portfolio_selection(
        context.workdir,
        state_root=context.state_root,
    )
    context.emit({
        "type": "idea.portfolio.formed",
        "team_root": str(team_root),
        "width": DEFAULT_PORTFOLIO_SIZE,
        "route_count": DEFAULT_PORTFOLIO_SIZE,
        "task_count": DEFAULT_PORTFOLIO_SIZE * 2,
        "selection": selection or {},
        "policy": SELECTION_POLICY,
        "text": (
            f"idea portfolio selected {selection['route_id']}"
            if selection
            else (
                "formed fixed twelve-route portfolio; selector starts after "
                "all twelve independent reviews finish"
            )
        ),
    })
