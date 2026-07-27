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
from argus_skill.skills.loop_skill_selection import (
    _ensure_playground_reviewer_reference,
    _prepare_playground_primary_skills,
)
from argus_skill.skills.role_match import match_role_skills
from argus_skill.skills.stage_machine import resolve_stage_checklist_contract
from argus_skill.skills.store import Skill, SkillStore
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


def test_all_chemistry_markdown_has_parseable_matcher_metadata() -> None:
    parsed = {
        name: Skill.parse(text, name)
        for name, text in iter_domain_skill_texts("chemistry")
        if name.endswith(".md")
    }

    assert parsed
    assert all(skill.name for skill in parsed.values())
    assert all(skill.description for skill in parsed.values())
    assert all(skill.category for skill in parsed.values())
    assert all(skill.version >= 1 for skill in parsed.values())
    assert all(len(skill.description) <= 240 for skill in parsed.values())
    assert len({skill.name for skill in parsed.values()}) == len(parsed)


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


def test_nested_domain_skills_are_discovered_as_engineer_role(tmp_path: Path) -> None:
    seed_context_skills(
        tmp_path,
        "research",
        domain="chemistry",
        overwrite=True,
    )

    summaries = SkillStore(tmp_path).list_summaries()
    by_name = {row["name"]: row for row in summaries}

    assert by_name["Materials Discovery and Optimization Workflow"]["role"] == "engineer"
    assert by_name["MOF Framework Identity Node Linker and Topology"]["role"] == "engineer"
    assert by_name["Biochemistry and Chemical Biology Evidence Review"]["role"] == "reviewer"


def test_context_skill_seeding_combines_research_and_chemistry(
    tmp_path: Path,
) -> None:
    context_names = {
        name for name, _ in iter_context_skill_texts("research", "chemistry")
    }
    result = seed_builtin_skills_for_context(
        tmp_path,
        "research",
        domain="chemistry",
        overwrite=True,
    )

    assert ALL_REQUIRED_SKILLS <= context_names
    assert ALL_REQUIRED_SKILLS <= set(result)
    for name in ALL_REQUIRED_SKILLS:
        assert (tmp_path / name).is_file()


def test_tool_skills_are_matchable_but_not_role_banner(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")
    seed_context_skills(
        tmp_path / "skills",
        "research",
        domain="chemistry",
        overwrite=True,
    )

    summaries = SkillStore(tmp_path / "skills").list_summaries()
    tool_names = {
        summary["name"]
        for summary in summaries
        if "/tools/" in str(summary["path"]).replace("\\", "/")
    }
    context = resolve_role_prompt(mission_request(tmp_path))

    assert len(tool_names) == len(CHEMISTRY_TOOL_SKILLS)
    assert "RDKit Molecular Integrity" in tool_names
    assert "RDKit Molecular Integrity" not in context.role_banner


def test_matcher_supports_positive_adjacent_and_cross_domain_scenarios(
    tmp_path: Path,
) -> None:
    seed_context_skills(
        tmp_path,
        "research",
        domain="chemistry",
        overwrite=True,
    )
    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(
            message=json.dumps(
                {
                    "matched": [
                        {
                            "name": "Organic Retrosynthesis and Route Design",
                            "fit": "high",
                            "why": "small-molecule route design",
                        }
                    ]
                }
            )
        ),
    )
    backend.queue(
        "matcher",
        CannedResponse(message=json.dumps({"matched": []})),
    )
    backend.queue(
        "matcher",
        CannedResponse(
            message=json.dumps(
                {
                    "matched": [
                        {
                            "name": "CIF and Crystal Structure Validation",
                            "fit": "high",
                            "why": "validate source CIF",
                        },
                        {
                            "name": "MOF Porosity Adsorption and Structure Property Workflow",
                            "fit": "high",
                            "why": "analyze activated framework porosity",
                        },
                    ]
                }
            )
        ),
    )
    store = SkillStore(tmp_path, runner=backend, matcher_model="matcher")

    organic = match_role_skills(
        store,
        role="engineer",
        task="Design and validate a retrosynthetic route for a chiral small molecule.",
    )
    adjacent_negative = match_role_skills(
        store,
        role="engineer",
        task="Fit a battery capacity-fade forecast from repeated cell cycling.",
    )
    cross_domain = match_role_skills(
        store,
        role="engineer",
        task=(
            "Validate a deposited MOF CIF, then analyze geometric porosity and compare "
            "the activated sample with a nitrogen adsorption isotherm."
        ),
    )

    assert [skill.name for skill in organic.primary_skills] == [
        "Organic Retrosynthesis and Route Design"
    ]
    assert adjacent_negative.skills == []
    assert {skill.name for skill in cross_domain.primary_skills} == {
        "CIF and Crystal Structure Validation",
        "MOF Porosity Adsorption and Structure Property Workflow",
    }


def test_reviewer_match_keeps_domain_review_primary_and_workflow_reference(
    tmp_path: Path,
) -> None:
    seed_context_skills(
        tmp_path,
        "research",
        domain="chemistry",
        overwrite=True,
    )
    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(
            message=json.dumps(
                {
                    "matched": [
                        {
                            "name": "MOF and Reticular Chemistry Evidence Review",
                            "fit": "high",
                            "why": "MOF evidence review",
                        },
                        {
                            "name": "MOF Framework Identity Node Linker and Topology",
                            "fit": "high",
                            "why": "engineer workflow context",
                        },
                    ]
                }
            )
        ),
    )
    store = SkillStore(tmp_path, runner=backend, matcher_model="matcher")

    match = match_role_skills(
        store,
        role="reviewer",
        task="Review node-linker decomposition and topology assignment for this MOF.",
    )

    assert [skill.name for skill in match.primary_skills] == [
        "MOF and Reticular Chemistry Evidence Review"
    ]
    assert [skill.name for skill in match.reference_skills] == [
        "MOF Framework Identity Node Linker and Topology"
    ]


def test_playground_match_is_explicit_and_keeps_reviewer_gate_reference(
    tmp_path: Path,
) -> None:
    seed_context_skills(
        tmp_path,
        "research",
        domain="chemistry",
        overwrite=True,
    )
    backend = MemoryBackend()
    backend.queue(
        "matcher",
        CannedResponse(
            message=json.dumps(
                {
                    "matched": [
                        {
                            "name": "Chemistry Playground Bounded Hypothesis Probe",
                            "fit": "high",
                            "why": "explicit bounded Chem Playground request",
                        },
                        {
                            "name": "Chemistry Playground Promotion Gate",
                            "fit": "high",
                            "why": "mandatory independent promotion boundary",
                        },
                    ]
                }
            )
        ),
    )
    backend.queue("matcher", CannedResponse(message=json.dumps({"matched": []})))
    store = SkillStore(tmp_path, runner=backend, matcher_model="matcher")

    explicit = match_role_skills(
        store,
        role="engineer",
        task=(
            "Create a Chem Playground candidate to computationally probe a speculative "
            "electrolyte decomposition hypothesis under a bounded budget."
        ),
    )
    ordinary = match_role_skills(
        store,
        role="engineer",
        task="Analyze routine battery cycling data and report capacity fade.",
    )

    assert [skill.name for skill in explicit.primary_skills] == [
        "Chemistry Playground Bounded Hypothesis Probe"
    ]
    assert [skill.name for skill in explicit.reference_skills] == [
        "Chemistry Playground Promotion Gate"
    ]
    assert explicit.primary_skills[0].protected is True
    assert explicit.reference_skills[0].protected is True
    ordinary_primary = Skill(
        name="RDKit Molecular Operations",
        description="ordinary chemistry tool",
        category="chemistry-tool",
        content="Use RDKit for deterministic molecular operations.",
    )
    required_references = _ensure_playground_reviewer_reference(
        explicit.primary,
        [ordinary_primary],
    )
    assert [skill.name for skill in required_references] == [
        "Chemistry Playground Promotion Gate"
    ]
    assert required_references[0].protected is True
    reordered = _prepare_playground_primary_skills(
        [ordinary_primary, explicit.primary_skills[0]]
    )
    assert reordered[0].name == "Chemistry Playground Bounded Hypothesis Probe"
    assert len(reordered) == 1
    unprotected = Skill(
        name=explicit.primary.name,
        description=explicit.primary.description,
        category=explicit.primary.category,
        content=explicit.primary.content,
        version=explicit.primary.version,
        protected=False,
    )
    with pytest.raises(RuntimeError, match="untrusted"):
        _prepare_playground_primary_skills([unprotected])
    assert ordinary.skills == []


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
        row["name"] == "RDKit Molecular Integrity" and row["layer"] == "vertical"
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
