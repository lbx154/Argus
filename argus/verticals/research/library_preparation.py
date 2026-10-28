"""Research-only Skill preparation for the four-stage workflow."""
from __future__ import annotations

from ...core.vertical_contract import VerticalLibraryContext

STAGE_PLAYBOOK_PATHS: dict[str, str] = {
    stage: f"research-{stage}-playbook.md"
    for stage in ("idea", "experiment", "paper", "review")
}


def prepare_skill_libraries(context: VerticalLibraryContext) -> None:
    """Prepare only the active stage's research Skills."""

    playbook = STAGE_PLAYBOOK_PATHS.get(context.stage)
    if playbook:
        context.required_skill_paths.append(playbook)

    if not context.paper_mission:
        return
    from ...skills.stage_machine import migrate_legacy_research_stage

    migrate_legacy_research_stage(context.state_root)
