"""Manager-selected vertical Skills for one worker, independent of campaign topology."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..skills.builtins import seed_context_skills
from ..skills.layered import LayeredSkillStore, shared_skill_scope_dir
from ..skills.role_library import RoleSkillLibraries, role_skill_libraries
from ..skills.vertical_select import (
    available_vertical_purposes,
    resolve_domain_if_decided,
    resolve_vertical_if_decided,
)


def self_skill_context(
    manager: Any,
    *,
    vertical: str | None = None,
    task: str = "",
    role: str = "engineer",
    on_event: Callable[[dict], object] | None = None,
) -> RoleSkillLibraries:
    """Refresh discovery roots without changing pipeline or Manager session state.

    None reuses the project's decision for legacy/direct callers. An explicit
    empty selection means no match on this turn, never the previous vertical.
    """
    from ..verticals._base import load_vertical, vertical_role_banner
    from ..verticals._data_domain import (
        list_selectable_data_domain_summaries,
        materialize_learned_data_domain,
    )

    root = Path(manager.project_root)
    learned = Path(manager.learned_vertical_root)
    persisted = resolve_vertical_if_decided(root) or ""
    selected = persisted if vertical is None else vertical
    domain = (resolve_domain_if_decided(root) or "") if selected and selected == persisted else ""
    catalog = {**available_vertical_purposes(),
               **list_selectable_data_domain_summaries(root, learned_root=learned)}
    if selected and selected not in catalog:
        raise ValueError(f"Unavailable SELF vertical: {selected}")
    store = manager.skill_store
    banner = ""
    if selected:
        materialize_learned_data_domain(learned, root, selected)
        banner = vertical_role_banner(load_vertical(selected, project_root=root), role)
    if store is not None and (selected or isinstance(store, LayeredSkillStore)):
        layered = isinstance(store, LayeredSkillStore)
        global_dir = store.global_.skills_dir if layered else store.skills_dir
        vertical_dir = shared_skill_scope_dir(global_dir, domain or selected)
        if selected and vertical_dir is not None:
            seed_context_skills(vertical_dir, selected, domain=domain or None)
        store = LayeredSkillStore(
            project_dir=store.project.skills_dir if layered else root / "skills",
            global_dir=global_dir,
            vertical_dir=vertical_dir,
            native_project_dir=Path(manager.execution_workdir) / ".agents" / "skills",
            execution_project_root=Path(manager.execution_workdir),
        )

    def emit(event: dict) -> None:
        if on_event is not None:
            on_event({**event, "vertical": selected})

    libraries = role_skill_libraries(store, role="self", task=task, on_event=emit)
    if selected:
        libraries.block = (
            f"## Vertical for this single-agent task: {selected}\n"
            "Apply the relevant domain methods, input requirements and checks within "
            "the requested scope. Read the matching Skills before doing the task. "
            "For a small request, use only the necessary part of the workflow. "
            "If a loaded Skill's explicit rule already invalidates the input or "
            "conclusively answers the requested check, report that result and stop; "
            "do not gather more sources, create extra artifacts or run later stages. "
            "This is one worker: do not create a Team, advance campaign stages, or "
            "claim independent review. Flag checks that need additional evidence.\n"
            + (f"\n{banner}\n" if banner else "")
            + "\n" + libraries.block
        )
    return libraries
