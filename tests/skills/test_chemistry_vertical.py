from __future__ import annotations

from pathlib import Path

from argus_skill.skills.builtins import (
    iter_vertical_skill_texts,
    seed_builtin_skills_for_vertical,
)
from argus_skill.skills.stage_machine import (
    ChecklistLoadState,
    resolve_stage_checklist_contract,
)
from argus_skill.skills.vertical_select import (
    VERTICAL_PURPOSES,
    VERTICALS,
    persist_vertical,
    require_vertical,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_items,
    vertical_checklist_stage_order,
    vertical_completion_gate,
    vertical_requires_independent_review,
    vertical_role_banner,
    vertical_workflow_mode,
)

CHEMISTRY_SKILLS = {
    "manager/chemistry-manager.md",
    "planner/chemistry-planning.md",
    "engineer/chemistry-execution.md",
    "engineer/chemistry-toolkit.md",
    "reviewer/chemistry-review.md",
    "scientist/chemistry-distillation.md",
    "scientist/chemistry-adaptation.md",
}


def test_chemistry_is_registered_as_independently_reviewed_vertical() -> None:
    assert "chemistry" in VERTICALS
    assert "chemistry" in VERTICAL_PURPOSES
    assert set(VERTICAL_PURPOSES) == set(VERTICALS)
    assert require_vertical("chemistry") == "chemistry"

    module = load_vertical("chemistry")
    assert module.STAGE_ORDER == ("frame", "investigate", "review")
    assert vertical_checklist_stage_order(module) == (
        "frame",
        "investigate",
        "review",
    )
    assert vertical_workflow_mode(module) == "proportional"
    assert vertical_completion_gate(module) == "none"
    assert vertical_requires_independent_review(module) is True


def test_chemistry_vertical_contains_only_contract_skills_and_metadata() -> None:
    root = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "verticals"
        / "chemistry"
    )
    files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert files == {
        "__init__.py",
        "stages.py",
        *(f"skills/{name}" for name in CHEMISTRY_SKILLS),
    }


def test_chemistry_roles_load_concise_domain_context() -> None:
    module = load_vertical("chemistry")

    manager = " ".join(vertical_role_banner(module, "manager").split())
    planner = " ".join(vertical_role_banner(module, "planner").split())
    engineer = " ".join(vertical_role_banner(module, "engineer").split())
    reviewer = " ".join(vertical_role_banner(module, "reviewer").split())
    scientist_create = " ".join(
        vertical_role_banner(module, "scientist_create").split()
    )
    scientist_adapt = " ".join(vertical_role_banner(module, "scientist").split())

    assert "This is chemistry work" in manager
    assert "options, not mandatory phases" in planner
    assert "observed, computed, simulated, predicted" in engineer
    assert "agent-designed fixed policy" in engineer
    assert "requested experiment tests online Argus decisions" in engineer
    assert "same user is useful interface separation" in engineer
    assert "Review the chemistry, not the paperwork" in reviewer
    assert "policy designed once by an agent" in reviewer
    assert "asked to evaluate online Argus control" in reviewer
    assert "same-user subprocess" in reviewer
    assert "without solving the current instance" in scientist_create
    assert "concrete failure exposes a real gap" in scientist_adapt


def test_chemistry_checklists_judge_results_not_process_files() -> None:
    items = vertical_checklist_items(load_vertical("chemistry"))

    assert {stage: len(stage_items) for stage, stage_items in items.items()} == {
        "frame": 3,
        "investigate": 4,
        "review": 4,
    }
    assert {
        stage: {item.id for item in stage_items}
        for stage, stage_items in items.items()
    } == {
        "frame": {
            "frame.question-system-observables",
            "frame.success-evidence-regime",
            "frame.feasible-capabilities",
        },
        "investigate": {
            "investigate.substantive-work",
            "investigate.input-method-fidelity",
            "investigate.evaluation-controls",
            "investigate.adaptive-evidence",
        },
        "review": {
            "review.scientific-fidelity",
            "review.execution-evidence",
            "review.evaluation-integrity",
            "review.outcome-honest",
        },
    }
    rendered = "\n".join(
        item.statement + " " + item.evidence_hint
        for stage_items in items.values()
        for item in stage_items
    )
    for process_artifact in (
        "TOOL_MANIFEST",
        "EVIDENCE_PACKET",
        "CLAIM_LEDGER",
        "AUDIT_REPORT",
        ".json",
        ".csv",
    ):
        assert process_artifact not in rendered
    assert "agent-designed fixed rule is not online agent control" in rendered
    assert "same-user subprocess is not an adversarially sealed evaluator" in rendered
    assert "online-agent objective is not silently replaced" in rendered
    assert "a frozen policy does not satisfy it" in rendered


def test_chemistry_stage_checks_are_structural_only() -> None:
    module = load_vertical("chemistry")

    assert module.STAGE_CHECKS == {
        stage: [
            (
                "Pipeline state present",
                "test -f research/PIPELINE_STATE.json",
            )
        ]
        for stage in module.STAGE_ORDER
    }


def test_chemistry_persistence_loads_required_review_checklist(
    tmp_path: Path,
) -> None:
    persist_vertical(tmp_path, "chemistry")

    contract = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert contract.state is ChecklistLoadState.LOADED
    assert contract.checklist_optional is False
    assert {item.id for item in contract.items} == {
        "review.scientific-fidelity",
        "review.execution-evidence",
        "review.evaluation-integrity",
        "review.outcome-honest",
    }


def test_chemistry_toolkit_is_seeded_with_verified_integration_caveats(
    tmp_path: Path,
) -> None:
    assert {name for name, _ in iter_vertical_skill_texts("chemistry")} == (
        CHEMISTRY_SKILLS
    )

    seed_builtin_skills_for_vertical(tmp_path, "chemistry", overwrite=True)
    toolkit = (
        tmp_path / "engineer" / "chemistry-toolkit.md"
    ).read_text(encoding="utf-8")

    assert "pip install chemcrow" in toolkit
    assert "does not reproduce the published" in toolkit
    assert "pip install olymp" in toolkit
    assert "mims-harvard/TDC" in toolkit
    assert "BenevolentAI/guacamol_baselines" in toolkit
    assert "supporting" in toolkit
    assert "simple implementation" in toolkit
    assert "instrument-side limits and interlocks" in toolkit
    assert "agent-designed policy" in toolkit
    assert "route each budgeted decision through the live" in toolkit
    assert "same-user subprocess is interface separation" in toolkit
