"""Research-only venue and idea preparation hooks."""
from __future__ import annotations

import os

from ...core.vertical_contract import VerticalLibraryContext

_FALSE = frozenset({"0", "false", "no", "off"})
_VENUE_STAGES = frozenset({"research", "plan", "benchmark", "run", "analysis"})


def _enabled(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in _FALSE


def prepare_skill_libraries(context: VerticalLibraryContext) -> None:
    """Prepare live research evidence before Agents inspect their libraries."""
    if context.workflow_mode == "direct" or not context.paper_mission:
        return
    from ...core.research_contract import resolve_research_target_level

    if resolve_research_target_level(context.workdir) == "exploratory":
        return
    if context.stage in {"plan", "benchmark", "run"}:
        context.required_skill_paths.extend((
            "engineer/training-infrastructure-guide.md",
            "engineer/hypothesis-implementation-contract.md",
        ))
    from .idea_portfolio import (
        DEFAULT_PORTFOLIO_SIZE,
        SELECTION_POLICY,
        ensure_idea_portfolio,
        idea_portfolio_selection,
        portfolio_required,
    )

    portfolio_active = (
        context.stage == "research" and portfolio_required(context.state_root)
    )
    if portfolio_active:
        context.required_skill_paths.extend((
            "engineer/idea-discovery.md",
            "engineer/idea-creator.md",
        ))
        if context.team_task_id:
            context.emit({
                "type": "idea.portfolio.nested_skipped",
                "team_task_id": context.team_task_id,
                "text": "team worker reused the parent portfolio without recursive fanout",
            })
        else:
            context.required_skill_paths.append("agent-team-lead.md")
            team_root = ensure_idea_portfolio(
                context.workdir,
                direction=context.direction,
            )
            selection = idea_portfolio_selection(context.workdir)
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
    if context.team_task_id:
        return
    if (
        _enabled("ARGUS_SKILL_VENUE_RESEARCH")
        and context.stage in _VENUE_STAGES
    ):
        from .venue_research import (
            needs_venue_research,
            research_venue_profile,
        )

        if needs_venue_research(context.workdir):
            context.emit({
                "type": "venue.research.started",
                "text": "live web search: selecting/researching target venue",
            })
            ok = research_venue_profile(
                context.runner,
                context.workdir,
                model=context.model,
            )
            context.emit({
                "type": "venue.research.completed",
                "ok": ok,
                "text": (
                    "built research/VENUE_PROFILE.json"
                    if ok
                    else "venue research produced no profile"
                ),
            })
    if (
        _enabled("ARGUS_SKILL_IDEA_SEARCH")
        and context.stage == "research"
        and not portfolio_active
    ):
        from .idea_search import _already_seeded, augment_idea_candidates

        if not _already_seeded(context.workdir):
            context.emit({
                "type": "idea.search.started",
                "text": "live web search: seeding candidate ideas",
            })
            count = augment_idea_candidates(
                context.runner,
                context.workdir,
                direction=context.direction,
                model=context.model,
            )
            context.emit({
                "type": "idea.search.completed",
                "count": count,
                "text": f"appended {count} candidate idea(s)",
            })
