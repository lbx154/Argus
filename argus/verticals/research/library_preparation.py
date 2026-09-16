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
        SELECTION_POLICY,
        ensure_idea_portfolio,
        idea_portfolio_selection,
        migrate_legacy_idea_selection,
        portfolio_required,
        portfolio_route_count,
        portfolio_size,
        portfolio_width,
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
    try:
        display_root = team_root.relative_to(context.workdir)
    except ValueError:
        display_root = team_root
    context.prompt_blocks.append(
        "## Canonical research idea portfolio\n"
        f"- The runtime has already formed the only authorized Idea portfolio at "
        f"`{display_root}`.\n"
        "- Inspect and settle that exact team. Do not call `team form`, create a "
        "second portfolio, or use a different `.argus/teams/...` path.\n"
        "- If the mission contract names another team path, that path is stale and "
        "does not authorize a replacement; the canonical runtime-owned path above "
        "takes precedence.\n"
        "- While its workers run, Argus waits for them itself and calls you only "
        "when the team has finished or needs attention (see External work "
        "status). Do not poll `team status`, tail worker logs, inspect Argus's "
        "own source, or resize the pool; its width is fixed by the host's "
        "provider capacity.\n"
        "- Do not launch a shell command or background subagent that polls for the "
        "selector. The resident Curator owns portfolio progress. When it is still "
        "running, report its exact durable state and yield the turn."
    )
    selection = idea_portfolio_selection(
        context.workdir,
        state_root=context.state_root,
    )
    route_count = portfolio_route_count(team_root) or portfolio_size()
    context.emit({
        "type": "idea.portfolio.formed",
        "team_root": str(team_root),
        "width": portfolio_width(team_root),
        "route_count": route_count,
        "task_count": route_count * 2,
        "selection": selection or {},
        "policy": SELECTION_POLICY,
        "text": (
            f"idea portfolio selected {selection['route_id']}"
            if selection
            else (
                f"formed {route_count}-route portfolio; selector starts after "
                f"all {route_count} independent reviews finish"
            )
        ),
    })
