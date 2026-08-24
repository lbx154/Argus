"""Resolve vertical providers through the framework's narrow contract.

Every built-in, data-domain, or entry-point vertical declares stages, checklist
items, completion strength, and optional role/evidence hooks. Missing or broken
providers fail visibly; silently substituting another vertical changes the task.
"""
from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from types import ModuleType
from typing import TypeAlias

from ..core.vertical_contract import (
    IterationAssessment,
    VerticalContract,
    vertical_contract,
)
from ._data_domain import DataDomain, load_data_domain

log = logging.getLogger(__name__)

#: The safe fallback vertical: its stages module always imports.
DEFAULT_VERTICAL = "research"
VerticalDefinition: TypeAlias = ModuleType | DataDomain
_VERTICAL_IMPORT_ALIASES = {
    "digital_circuit_benchmark": "digital_circuit.benchmark",
}


def _normalize_vertical_name(name: object) -> str:
    """Lower/strip a vertical name and drop a trailing ``-needed`` sentinel."""
    if not isinstance(name, str):
        return DEFAULT_VERTICAL
    cleaned = name.strip().lower()
    if cleaned.endswith("-needed"):
        cleaned = cleaned[: -len("-needed")]
    return cleaned or DEFAULT_VERTICAL


def load_vertical(name: object, project_root: object = None) -> VerticalDefinition:
    """Resolve one in-tree, plugin, or project-local vertical provider."""
    cleaned = _normalize_vertical_name(name)
    import_name = _VERTICAL_IMPORT_ALIASES.get(cleaned, cleaned)
    module_name = f"argus_skill.verticals.{import_name}.stages"
    stages_path = os.path.join(
        os.path.dirname(__file__), *import_name.split("."), "stages.py"
    )
    if os.path.isfile(stages_path):
        try:
            return importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"vertical {cleaned!r} exists but failed to import: {exc}"
            ) from exc

    from ._registry import vertical_plugin

    plugin = vertical_plugin(cleaned)
    if plugin is not None:
        return plugin.module
    if project_root is not None:
        domain = load_data_domain(cleaned, project_root)
        if domain is not None:
            return domain
    raise LookupError(f"unknown vertical: {cleaned}")


def load_vertical_contract(
    name: object,
    project_root: object = None,
) -> VerticalContract:
    cleaned = _normalize_vertical_name(name)
    return vertical_contract(cleaned, load_vertical(cleaned, project_root=project_root))


# --- contract accessors retained for existing callers ---------------------


def _contract(mod: VerticalDefinition) -> VerticalContract:
    name = str(getattr(mod, "__name__", None) or getattr(mod, "name", "vertical"))
    return vertical_contract(name, mod)


def vertical_checklist_stage_order(mod: VerticalDefinition) -> tuple[str, ...]:
    return _contract(mod).stage_order


def vertical_checklist_items(mod: VerticalDefinition) -> dict:
    return _contract(mod).checklist_items


def vertical_checklist_optional_stages(
    mod: VerticalDefinition,
) -> frozenset[str]:
    """Return stages whose checklist is explicitly declared optional."""
    return _contract(mod).checklist_optional_stages


def vertical_stage_aliases(mod: VerticalDefinition) -> dict[str, str]:
    """Return non-canonical stage names mapped to canonical stage names."""
    return dict(_contract(mod).stage_aliases or {})


def vertical_role_banner(mod: VerticalDefinition, role: str) -> str:
    return _contract(mod).banner(role)


def vertical_requires_independent_review(mod: VerticalDefinition) -> bool:
    """Return whether every mission in this vertical requires a Reviewer."""
    return _contract(mod).requires_independent_review


def vertical_completion_gate(mod: VerticalDefinition) -> str:
    return _contract(mod).completion_gate


def vertical_mission_kind(mod: VerticalDefinition) -> str:
    return _contract(mod).mission_kind


def vertical_is_paper_mission(mod: VerticalDefinition) -> bool:
    return _contract(mod).paper_mission


def vertical_verification_stage_profiles(
    mod: VerticalDefinition,
) -> dict[str, str]:
    return dict(_contract(mod).verification_stage_profiles or {})


def vertical_completion_contract_version(mod: VerticalDefinition) -> int:
    """Return the optional versioned final-stage completion contract."""
    return _contract(mod).completion_contract_version


def vertical_research_target_levels(mod: VerticalDefinition) -> tuple[str, ...]:
    """Return the research target levels supported by this vertical."""
    return _contract(mod).research_target_levels


def vertical_workflow_mode(mod: VerticalDefinition) -> str:
    """Return the vertical's supported workflow mode."""
    return _contract(mod).workflow_mode


def vertical_search_altitude(mod: VerticalDefinition, project_root: object) -> str:
    return _contract(mod).altitude(project_root)


def vertical_prepare_mission(
    mod: VerticalDefinition,
    *,
    stage: str,
    project_root: Path,
    state_root: Path,
    mission: object,
) -> str:
    """``mission`` is the claimed backlog item; see ``VerticalContract``."""
    return _contract(mod).prepare_mission(
        stage=stage,
        project_root=project_root,
        state_root=state_root,
        mission=mission,
    )


def vertical_mission_prelude(
    *,
    vertical_root: Path,
    project_root: Path,
    state_root: Path,
    stage: str,
    mission: object,
) -> str:
    """Resolve the active vertical and return its block for *this* mission.

    One seam, two callers. The daemon's supervisor builds this for a claimed
    backlog item; ``team/teammate_entry.py`` builds it for the board task a
    dispatched teammate owns. Both need the identical three steps — resolve the
    project's persisted vertical, load its contract, forward the hook by
    keyword — and computing them twice is how the two drift: a teammate that
    resolved the vertical differently would be reading a different project than
    the Engineer that dispatched it.

    ``vertical_root`` is where the Manager's ``PIPELINE_STATE.json`` decision
    lives; ``project_root`` is the tree this mission actually works in. They are
    separate parameters because the supervisor already passes two different
    paths (session artifact root vs. adopted mission workdir), and collapsing
    them here would silently retarget it.

    Deliberately unguarded, and that is the whole point of the hook's contract
    (see ``VerticalContract.prepare_mission``): a stale out-of-tree provider
    halts the run with a ``TypeError`` naming the argument to add, rather than
    being quietly demoted to mission-blind for the life of the project. A caller
    that cannot afford to die — a single subordinate teammate, say — owns that
    decision at its own call site, where the trade is visible.
    """
    from ..skills.vertical_select import resolve_vertical

    contract = load_vertical_contract(
        resolve_vertical(vertical_root), project_root=vertical_root
    )
    return contract.prepare_mission(
        stage=stage,
        project_root=project_root,
        state_root=state_root,
        mission=mission,
    )


def vertical_planner_task_issues(
    mod: VerticalDefinition,
    *,
    stage: str,
    project_root: Path,
    task: object,
) -> tuple[str, ...]:
    return _contract(mod).planner_task_issues(stage, project_root, task)


def vertical_stage_primary_deliverables(
    mod: VerticalDefinition,
    *,
    stage: str,
) -> tuple[str, ...]:
    return _contract(mod).primary_deliverables(stage)


def vertical_stage_completion_issues(
    mod: VerticalDefinition,
    *,
    stage: str,
    project_root: Path,
    state_root: Path | None = None,
) -> tuple[str, ...]:
    """Run the provider's deterministic pre-completion validator, if any."""
    return _contract(mod).completion_issues(
        stage,
        project_root,
        state_root=state_root,
    )


def vertical_iteration_assessment(
    mod: VerticalDefinition,
    *,
    stage: str,
    scope: str,
    project_root: Path,
    state_root: Path,
    mission: object,
    outcome: object,
) -> IterationAssessment | None:
    """Route a would-be terminal result through the active vertical."""
    return _contract(mod).assess_iteration(
        stage=stage,
        scope=scope,
        project_root=project_root,
        state_root=state_root,
        mission=mission,
        outcome=outcome,
    )


def vertical_adopt_operator_objective(
    mod: VerticalDefinition,
    *,
    project_root: Path,
    request: str,
) -> bool:
    """Hand the vertical the operator's request so it can record its objective.

    Returns whether the vertical declares an adopter at all. Verticals that
    have nothing to choose declare none, and this is a no-op for them.
    """
    return _contract(mod).adopt_operator_objective(project_root, request)


__all__ = [
    "DEFAULT_VERTICAL",
    "VerticalContract",
    "VerticalDefinition",
    "load_vertical",
    "load_vertical_contract",
    "vertical_adopt_operator_objective",
    "vertical_checklist_stage_order",
    "vertical_checklist_items",
    "vertical_checklist_optional_stages",
    "vertical_role_banner",
    "vertical_requires_independent_review",
    "vertical_completion_contract_version",
    "vertical_completion_gate",
    "vertical_mission_kind",
    "vertical_is_paper_mission",
    "vertical_verification_stage_profiles",
    "vertical_mission_prelude",
    "vertical_research_target_levels",
    "vertical_prepare_mission",
    "vertical_planner_task_issues",
    "vertical_workflow_mode",
    "vertical_search_altitude",
    "vertical_stage_completion_issues",
    "vertical_stage_primary_deliverables",
]
