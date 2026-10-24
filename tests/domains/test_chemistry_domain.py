from __future__ import annotations

from pathlib import Path

import pytest

from argus.domains import (
    domain_checklist_items,
    domain_role_banner,
    load_domain,
)
from argus.roles.prompts import resolve_role_prompt
from argus.roles.prompts.engineer import mission_request
from argus.skills.builtins import (
    remove_unmodified_inactive_context_skill_seeds,
    seed_context_skills,
)
from argus.skills.layered import LayeredSkillStore, shared_skill_scope_dir
from argus.skills.stage_machine import resolve_stage_checklist_contract
from argus.skills.vertical_select import (
    persist_vertical,
    resolve_skill_scope,
)


def test_non_research_vertical_rejects_domain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="require vertical='research'"):
        persist_vertical(tmp_path, "software", domain="chemistry")


def test_domain_role_context_composes_with_research_prompt(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")

    context = resolve_role_prompt(mission_request(tmp_path))
    chemistry = load_domain("chemistry")
    engineer_banner = domain_role_banner(chemistry, "engineer")

    assert context.vertical == "research"
    assert context.completion_gate == "certified"
    assert engineer_banner in context.role_banner
    assert "Load the narrowest matched domain Skill" in engineer_banner
    assert "domain:chemistry:banner:engineer" in context.fragment_ids
    assert "vertical:chemistry" not in " ".join(context.fragment_ids)


def test_domain_checklist_is_mandatory_scientific_floor(tmp_path: Path) -> None:
    persist_vertical(tmp_path, "research", domain="chemistry")
    chemistry = domain_checklist_items(load_domain("chemistry"))

    idea = resolve_stage_checklist_contract("idea", project_root=tmp_path)
    experiment = resolve_stage_checklist_contract(
        "experiment", project_root=tmp_path
    )
    review = resolve_stage_checklist_contract("review", project_root=tmp_path)

    assert {item.id for item in chemistry["idea"]} <= {item.id for item in idea.items}
    assert {item.id for item in chemistry["experiment"]} <= {
        item.id for item in experiment.items
    }
    assert {item.id for item in chemistry["review"]} <= {
        item.id for item in review.items
    }
    assert "experiment.implementation" in {item.id for item in experiment.items}
    assert "experiment.chemistry-primary-evidence" in {
        item.id for item in experiment.items
    }
    assert {
        stage
        for stage, items in chemistry.items()
        if items
    } == {"idea", "experiment", "review"}


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
