from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.domains import (
    BUILTIN_DOMAINS,
    DOMAIN_PURPOSES,
    domain_checklist_items,
    domain_role_banner,
    load_domain,
)
from argus_skill.roles.prompts import resolve_role_prompt
from argus_skill.roles.prompts.engineer import mission_request
from argus_skill.skills.builtins import (
    iter_context_skill_texts,
    iter_domain_skill_texts,
    remove_unmodified_inactive_context_skill_seeds,
    seed_builtin_skills_for_context,
    seed_context_skills,
)
from argus_skill.skills.layered import LayeredSkillStore, shared_skill_scope_dir
from argus_skill.skills.stage_machine import resolve_stage_checklist_contract
from argus_skill.skills.store import SkillStore
from argus_skill.skills.vertical_select import (
    VERTICALS,
    UnknownVerticalError,
    persist_vertical,
    require_vertical,
    resolve_domain_if_decided,
    resolve_skill_scope,
)
from argus_skill.verticals._base import (
    load_vertical,
    vertical_checklist_stage_order,
    vertical_completion_gate,
)

CORE_CHEMISTRY_SKILLS = {
    "manager/chemistry-manager.md",
    "planner/chemistry-planning.md",
    "engineer/chemistry-execution.md",
    "engineer/chemistry-toolkit.md",
    "reviewer/chemistry-review.md",
    "scientist/chemistry-distillation.md",
    "scientist/chemistry-adaptation.md",
}

PLAYGROUND_SKILLS = {
    "engineer/workflows/chemistry-playground.md",
    "reviewer/chemistry-playground-review.md",
}

CHEMISTRY_TOOL_SKILLS = {
    "engineer/tools/rdkit.md",
    "engineer/tools/openbabel.md",
    "engineer/tools/pubchem.md",
    "engineer/tools/chembl.md",
    "engineer/tools/ord.md",
    "engineer/tools/aizynthfinder.md",
    "engineer/tools/askcos.md",
    "engineer/tools/pyscf.md",
    "engineer/tools/psi4.md",
    "engineer/tools/orca.md",
    "engineer/tools/deepchem.md",
    "engineer/tools/tdc.md",
    "engineer/tools/guacamol.md",
    "engineer/tools/olympus.md",
    "engineer/tools/chemcrow.md",
    "engineer/tools/coscientist.md",
    "engineer/tools/chemos.md",
}

FOUNDATION_SKILLS = {
    "engineer/foundations/chemical-identity-and-representation.md",
    "engineer/foundations/units-conditions-and-normalization.md",
    "engineer/foundations/evidence-provenance-and-claim-levels.md",
    "engineer/foundations/uncertainty-and-applicability-domain.md",
    "engineer/foundations/dataset-curation-and-leakage.md",
    "engineer/foundations/computational-reproducibility.md",
    "engineer/foundations/chemical-risk-and-authorization.md",
    "engineer/foundations/failure-diagnosis-and-negative-results.md",
    "engineer/foundations/chemistry-workflow-output-contract.md",
}

DOMAIN_ENGINEER_SKILLS = {
    "organic_synthesis": {
        "engineer/organic_synthesis/reaction-identity-and-records.md",
        "engineer/organic_synthesis/retrosynthesis-and-route-design.md",
        "engineer/organic_synthesis/route-validation-and-experiment-design.md",
    },
    "materials_science": {
        "engineer/materials_science/materials-identity-processing-and-property-data.md",
        "engineer/materials_science/materials-discovery-and-optimization.md",
        "engineer/materials_science/processing-structure-property-validation.md",
    },
    "crystallography": {
        "engineer/crystallography/diffraction-and-crystal-identity.md",
        "engineer/crystallography/structure-solution-and-refinement.md",
        "engineer/crystallography/cif-and-structure-validation.md",
    },
    "mof_reticular_chemistry": {
        "engineer/mof_reticular_chemistry/framework-identity-node-linker-and-topology.md",
        "engineer/mof_reticular_chemistry/synthesis-activation-and-postsynthetic-evidence.md",
        "engineer/mof_reticular_chemistry/porosity-adsorption-and-structure-property.md",
        "engineer/mof_reticular_chemistry/mof-datasets-prediction-and-generation.md",
    },
    "computational_chemistry": {
        "engineer/computational_chemistry/computational-identity-normalization.md",
        "engineer/computational_chemistry/electronic-structure-simulation-workflow.md",
        "engineer/computational_chemistry/computational-validation-and-interpretation.md",
    },
    "batteries": {
        "engineer/batteries/battery-identity-and-data-normalization.md",
        "engineer/batteries/cycling-and-degradation-analysis.md",
        "engineer/batteries/battery-model-validation.md",
    },
    "characterization": {
        "engineer/characterization/characterization-data-and-sample-normalization.md",
        "engineer/characterization/modality-specific-interpretation-workflow.md",
        "engineer/characterization/characterization-validation-and-integration.md",
    },
    "biochemistry": {
        "engineer/biochemistry/biochemistry-system-and-assay-normalization.md",
        "engineer/biochemistry/biochemical-assay-and-mechanism-workflow.md",
        "engineer/biochemistry/biochemistry-structural-computational-evidence.md",
    },
}

SPECIALIZED_REVIEWER_SKILLS = {
    "reviewer/organic-synthesis-review.md",
    "reviewer/materials-science-review.md",
    "reviewer/crystallography-review.md",
    "reviewer/mof-reticular-chemistry-review.md",
    "reviewer/computational-review.md",
    "reviewer/battery-review.md",
    "reviewer/characterization-review.md",
    "reviewer/biochemistry-review.md",
}

ALL_DOMAIN_ENGINEER_SKILLS = set().union(*DOMAIN_ENGINEER_SKILLS.values())
ALL_REQUIRED_SKILLS = (
    CORE_CHEMISTRY_SKILLS
    | CHEMISTRY_TOOL_SKILLS
    | FOUNDATION_SKILLS
    | ALL_DOMAIN_ENGINEER_SKILLS
    | SPECIALIZED_REVIEWER_SKILLS
    | PLAYGROUND_SKILLS
)


def _chemistry_texts() -> dict[str, str]:
    return dict(iter_domain_skill_texts("chemistry"))


def test_chemistry_is_domain_not_peer_vertical() -> None:
    assert "chemistry" in BUILTIN_DOMAINS
    assert "chemistry" in DOMAIN_PURPOSES
    assert "chemistry" not in VERTICALS
    with pytest.raises(UnknownVerticalError):
        require_vertical("chemistry")


def test_research_owns_workflow_when_chemistry_is_active(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")

    payload = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    research = load_vertical("research", project_root=tmp_path)

    assert payload["vertical"] == "research"
    assert payload["domain"] == "chemistry"
    assert payload["current_stage"] == "research"
    assert resolve_domain_if_decided(tmp_path) == "chemistry"
    assert resolve_skill_scope(tmp_path) == "chemistry"
    assert vertical_checklist_stage_order(research) == (
        "research",
        "plan",
        "benchmark",
        "run",
        "analysis",
        "draft",
        "review",
        "submission",
    )
    assert vertical_completion_gate(research) == "full_paper"


def test_non_research_vertical_rejects_domain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require vertical='research'"):
        persist_vertical(tmp_path, "software", domain="chemistry")


def test_switching_to_non_research_clears_domain(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")
    persist_vertical(tmp_path, "software")

    payload = json.loads(
        (tmp_path / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )
    assert payload["vertical"] == "software"
    assert "domain" not in payload


def test_domain_role_context_composes_with_research_prompt(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")

    context = resolve_role_prompt(mission_request(tmp_path))
    chemistry = load_domain("chemistry")
    engineer_banner = domain_role_banner(chemistry, "engineer")

    assert context.vertical == "research"
    assert context.completion_gate == "full_paper"
    assert engineer_banner in context.role_banner
    assert "Load the narrowest matched domain Skill" in engineer_banner
    assert "domain:chemistry:banner:engineer" in context.fragment_ids
    assert "vertical:chemistry" not in " ".join(context.fragment_ids)


def test_domain_checklist_is_mandatory_scientific_floor(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")
    chemistry = domain_checklist_items(load_domain("chemistry"))

    plan = resolve_stage_checklist_contract("plan", project_root=tmp_path)
    run = resolve_stage_checklist_contract("run", project_root=tmp_path)
    review = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert {item.id for item in chemistry["plan"]} <= {item.id for item in plan.items}
    assert {item.id for item in chemistry["run"]} <= {item.id for item in run.items}
    assert {item.id for item in chemistry["review"]} <= {
        item.id for item in review.items
    }
    assert "plan.experiment" in {item.id for item in plan.items}
    assert "run.chemistry-primary-evidence" in {item.id for item in run.items}
    assert {
        stage
        for stage, items in chemistry.items()
        if items
    } == {"research", "plan", "benchmark", "run", "analysis", "review"}


def test_chemistry_package_contains_foundations_tools_and_eight_domains() -> None:
    texts = _chemistry_texts()
    names = set(texts)

    assert ALL_REQUIRED_SKILLS <= names
    assert len(DOMAIN_ENGINEER_SKILLS) == 8
    assert PLAYGROUND_SKILLS <= names
    assert {name for name in names if "playground" in name.casefold()} == PLAYGROUND_SKILLS


def test_all_chemistry_skills_use_minimal_agent_readable_metadata() -> None:
    import yaml

    documents = [
        text
        for name, text in iter_domain_skill_texts("chemistry")
        if name.endswith(".md")
    ]
    assert documents
    for text in documents:
        front = yaml.safe_load(text[4:].split("\n---\n", 1)[0])
        assert set(front) == {"name", "description"}


def test_domain_workflows_have_executable_contract_and_boundaries() -> None:
    texts = _chemistry_texts()
    required_sections = (
        "## When to use",
        "## Do not use",
        "## Scientific question",
        "## Required inputs",
        "## Output contract",
        "## Stop",
        "## Official references",
    )

    for name in sorted(ALL_DOMAIN_ENGINEER_SKILLS):
        text = texts[name]
        for section in required_sections:
            assert section in text, f"{name} missing {section}"
        normalized = " ".join(text.casefold().split())
        assert "evidence" in normalized


def test_tool_profiles_are_capability_profiles_not_install_scripts() -> None:
    texts = _chemistry_texts()

    for name in sorted(CHEMISTRY_TOOL_SKILLS):
        text = texts[name]
        assert "## When to use" in text
        assert "## Do not use" in text
        assert "## Minimum capability probe" in text
        assert "## Output contract" in text
        assert "## Official references" in text
        assert "pip install" not in text.casefold()
        assert "conda install" not in text.casefold()


def test_runtime_shared_scope_uses_domain_namespace(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    global_root = tmp_path / "global"
    project_skills = tmp_path / "project-state" / "skills"
    persist_vertical(project_root, "research", domain="chemistry")
    scope_dir = shared_skill_scope_dir(
        global_root,
        resolve_skill_scope(project_root),
    )
    assert scope_dir is not None
    seed_context_skills(
        scope_dir,
        "research",
        domain="chemistry",
        overwrite=True,
    )

    store = LayeredSkillStore(
        project_dir=project_skills,
        global_dir=global_root,
        vertical_dir=scope_dir,
    )
    summaries = store.list_summaries()

    assert scope_dir.name == "chemistry"
    assert any(
        row["name"].endswith("engineer/tools/rdkit")
        and row["layer"] == "vertical"
        for row in summaries
    )


def test_inactive_domain_cleanup_preserves_user_edits(tmp_path: Path) -> None:
    seed_context_skills(
        tmp_path,
        "research",
        domain="chemistry",
        overwrite=True,
    )
    edited = tmp_path / "engineer" / "tools" / "rdkit.md"
    removed = tmp_path / "engineer" / "tools" / "openbabel.md"
    edited.write_text(
        edited.read_text(encoding="utf-8") + "\nOperator note.\n",
        encoding="utf-8",
    )

    removed_names = remove_unmodified_inactive_context_skill_seeds(
        tmp_path,
        "research",
        active_domain=None,
    )

    assert edited.is_file()
    assert not removed.exists()
    assert "engineer/tools/openbabel.md" in removed_names
    assert "engineer/tools/rdkit.md" not in removed_names
