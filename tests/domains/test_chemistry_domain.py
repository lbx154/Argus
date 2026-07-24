from __future__ import annotations

import json
from pathlib import Path

import pytest

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
from argus_skill.skills.stage_machine import (
    resolve_stage_checklist_contract,
)
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

    assert context.vertical == "research"
    assert context.completion_gate == "full_paper"
    assert domain_role_banner(chemistry, "engineer") in context.role_banner
    assert "domain:chemistry:banner:engineer" in context.fragment_ids
    assert "vertical:chemistry" not in " ".join(context.fragment_ids)


def test_domain_checklist_is_mandatory_floor(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")
    chemistry = domain_checklist_items(load_domain("chemistry"))

    plan = resolve_stage_checklist_contract("plan", project_root=tmp_path)
    run = resolve_stage_checklist_contract("run", project_root=tmp_path)

    assert {item.id for item in chemistry["plan"]} <= {item.id for item in plan.items}
    assert {item.id for item in chemistry["run"]} <= {item.id for item in run.items}
    assert "plan.experiment" in {item.id for item in plan.items}
    assert "run.chemistry-online-control" in {item.id for item in run.items}


def test_chemistry_package_contains_core_role_skills() -> None:
    names = {name for name, _ in iter_domain_skill_texts("chemistry")}

    assert CORE_CHEMISTRY_SKILLS <= names
    assert CHEMISTRY_TOOL_SKILLS <= names
    assert {
        stage
        for stage, items in domain_checklist_items(load_domain("chemistry")).items()
        if items
    } == {"research", "plan", "benchmark", "run", "analysis", "review"}


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

    assert CORE_CHEMISTRY_SKILLS <= context_names
    assert CORE_CHEMISTRY_SKILLS <= set(result)
    for name in CORE_CHEMISTRY_SKILLS:
        assert (tmp_path / name).is_file()


def test_tool_skills_are_matchable_but_not_role_banner(
    tmp_path: Path,
) -> None:
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
        if "/tools/" in str(summary["path"])
    }
    context = resolve_role_prompt(mission_request(tmp_path))

    assert len(tool_names) == len(CHEMISTRY_TOOL_SKILLS)
    assert "RDKit Molecular Integrity" in tool_names
    assert "RDKit Molecular Integrity" not in context.role_banner


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


def test_toolkit_preserves_online_and_evaluator_boundaries() -> None:
    toolkit = dict(iter_domain_skill_texts("chemistry"))[
        "engineer/chemistry-toolkit.md"
    ]
    tools = dict(iter_domain_skill_texts("chemistry"))
    normalized = " ".join(toolkit.lower().split())

    assert "route each budgeted decision through the live" in normalized
    assert "same-user subprocess is interface separation" in normalized
    assert "pip install chemcrow" in tools["engineer/tools/chemcrow.md"]
    assert "pip install olymp" in tools["engineer/tools/olympus.md"]
