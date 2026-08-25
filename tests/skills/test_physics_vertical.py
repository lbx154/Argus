"""Contract tests for the lightweight physics vertical.

These are self-contained: they load the vertical directly and never read Phase 3
pipeline outputs or any large literature-distillation artifact.
"""

from __future__ import annotations

from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_items,
    vertical_checklist_stage_order,
    vertical_completion_gate,
    vertical_role_banner,
    vertical_workflow_mode,
)


def test_physics_vertical_can_be_imported() -> None:
    # Requirement 1: the physics vertical package + stages module import cleanly.
    import argus_skill.verticals.physics as physics_pkg
    from argus_skill.verticals.physics import stages

    assert stages.STAGE_ORDER == ("scope", "model", "execute", "review", "manuscript")
    assert physics_pkg.STAGE_ORDER == stages.STAGE_ORDER


def test_load_vertical_physics_resolves_to_physics_not_fallback() -> None:
    # Requirements 2 & 10 (loader side): load_vertical("physics") returns the
    # physics stages module and does NOT silently fall back to research or math.
    mod = load_vertical("physics")

    assert mod.__name__ == "argus_skill.verticals.physics.stages"
    assert mod.__name__ != "argus_skill.verticals.research.stages"
    assert mod.__name__ != "argus_skill.verticals.math.stages"


def test_physics_stage_contract_is_five_stages_ending_in_manuscript() -> None:
    # Requirement 3: STAGE_ORDER is the four dynamic-path stages plus the
    # mandatory terminal manuscript stage.
    mod = load_vertical("physics")

    five = ("scope", "model", "execute", "review", "manuscript")
    assert mod.STAGE_ORDER == five
    assert vertical_checklist_stage_order(mod) == five
    assert tuple(mod.CHECKLIST_ITEMS) == mod.STAGE_ORDER
    assert not hasattr(mod, "STAGE_CHECKS")
    assert not hasattr(mod, "REVIEWER_CHECKLISTS")


def test_physics_uses_reviewer_certified_non_paper_gate() -> None:
    # Requirement 4: completion_gate is "none" (not a paper/metric gate).
    mod = load_vertical("physics")

    gate = vertical_completion_gate(mod)
    assert gate == "none"
    assert gate not in {"metric", "certified"}


def test_physics_runs_in_proportional_workflow_mode() -> None:
    # Requirement 5: WORKFLOW_MODE is "proportional".
    mod = load_vertical("physics")

    assert mod.WORKFLOW_MODE == "proportional"
    assert vertical_workflow_mode(mod) == "proportional"


def test_physics_planner_banner_encodes_dynamic_route_and_no_fixed_pipeline() -> None:
    # Requirement 6: planner drives physics-specific route selection and rejects a
    # fixed paper pipeline.
    planner = vertical_role_banner(load_vertical("physics"), "planner")

    assert "Let the physical question choose" in planner
    for route in ("theory", "simulation", "data", "literature", "experiment"):
        assert route in planner
    assert "expected information gain" in planner
    assert "multiple mechanisms" in planner
    assert "rather than more paperwork" in planner


def test_physics_engineer_banner_encodes_units_equations_assumptions_evidence() -> None:
    # Requirement 7: engineer names units, equations, assumptions, evidence limits.
    engineer = vertical_role_banner(load_vertical("physics"), "engineer")

    assert "units" in engineer
    assert "equations" in engineer
    assert "assumptions" in engineer
    assert "evidence limits" in engineer
    assert "boundary/initial conditions" in engineer
    assert "residual" in engineer
    assert "convergence" in engineer
    assert "uncertainty" in engineer
    assert "provenance" in engineer
    assert "toy" in engineer
    assert "not physical evidence" in engineer


def test_physics_reviewer_banner_encodes_fidelity_units_bcic_evidence_and_antidrift() -> None:
    # Requirement 8: reviewer covers system fidelity, unit/dimension, BC/IC,
    # numerical/data evidence, anti-drift, and claim-boundary checks.
    reviewer = vertical_role_banner(load_vertical("physics"), "reviewer")

    assert "physical-system fidelity" in reviewer
    assert "dimensional consistency" in reviewer
    assert "boundary/initial conditions" in reviewer
    assert "numerical or data evidence" in reviewer
    assert "claim boundaries" in reviewer
    assert "agent-workflow" in reviewer
    assert "metadata-only" in reviewer
    assert "do not demand fixed tables, counts, or certificates" in reviewer
    assert "Ask for replan" in reviewer


def test_every_physics_stage_has_checklist_items() -> None:
    # Requirement 9: CHECKLIST_ITEMS covers all five stages, each non-empty.
    mod = load_vertical("physics")
    items = vertical_checklist_items(mod)

    assert set(items) == {"scope", "model", "execute", "review", "manuscript"}
    assert all(items[stage] for stage in mod.CHECKLIST_STAGE_ORDER)


def test_physics_reviewer_checklists_are_native_items() -> None:
    # The vertical-owned checklist is the single Reviewer contract surface.
    mod = load_vertical("physics")

    assert set(mod.CHECKLIST_ITEMS) == {
        "scope",
        "model",
        "execute",
        "review",
        "manuscript",
    }
    for items in mod.CHECKLIST_ITEMS.values():
        assert all(item.id and item.statement and item.evidence_hint for item in items)
