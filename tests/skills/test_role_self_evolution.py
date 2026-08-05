from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager import Manager
from argus_skill.planner import Planner
from argus_skill.reviewer import Reviewer
from argus_skill.roles.prompts.engineer import build_mission_prompt
from argus_skill.skills.missions import PlannerMission
from argus_skill.skills.role_memory import (
    project_role_skill_dir,
    role_skill_maintenance_block,
    role_skill_maintenance_enabled,
)
from argus_skill.skills.store import SkillStore


def test_global_role_self_evolution_ab_knob(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "0")
    assert role_skill_maintenance_enabled() is False
    assert Manager(memory_maintenance_enabled=None).memory_maintenance_enabled is False
    assert Planner(None, memory_maintenance_enabled=None).memory_maintenance_enabled is False

    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_POST_TASK_LEARNING", "1")
    assert role_skill_maintenance_enabled() is True
    assert Manager(memory_maintenance_enabled=None).memory_maintenance_enabled is True
    assert Planner(None, memory_maintenance_enabled=None).memory_maintenance_enabled is True


def test_role_skill_directories_are_isolated(tmp_path) -> None:
    store = SimpleNamespace(project=SimpleNamespace(skills_dir=tmp_path / "skills"))

    for role in ("manager", "planner", "engineer", "reviewer"):
        assert project_role_skill_dir(store, role) == (tmp_path / "skills" / role).resolve()


def test_role_maintenance_block_has_a_clean_ab_switch(tmp_path) -> None:
    store = SimpleNamespace(project=SimpleNamespace(skills_dir=tmp_path / "skills"))

    control = role_skill_maintenance_block(store, "reviewer", enabled=False)
    treatment = role_skill_maintenance_block(store, "reviewer", enabled=True)

    assert control == ""
    assert "## Reviewer self-evolution" in treatment
    assert str((tmp_path / "skills" / "reviewer").resolve()) in treatment
    assert "exactly `name` and `description` frontmatter" in treatment
    assert "`version`" not in treatment


def test_engineer_learning_targets_engineer_bucket(tmp_path) -> None:
    skill_dir = tmp_path / "skills" / "engineer"
    prompt = build_mission_prompt(
        task="repair the parser",
        skill_text="",
        next_action=None,
        require_post_task_learning=True,
        project_skill_dir=skill_dir,
    )

    assert f"Engineer Skill directory (project layer only): {skill_dir}" in prompt


def test_reviewer_learning_ab_switch_targets_reviewer_bucket(tmp_path) -> None:
    store = SkillStore(tmp_path / "skills")
    common = dict(
        objective="review the software patch",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="implementation complete",
        main_error=None,
        working_dir=tmp_path,
    )

    control = Reviewer(
        runner=None,
        skill_store=store,
        memory_maintenance_enabled=False,
    )._build_prompt(**common)
    treatment = Reviewer(
        runner=None,
        skill_store=store,
        memory_maintenance_enabled=True,
    )._build_prompt(**common)

    assert "Reviewer self-evolution" not in control
    assert "Reviewer self-evolution" in treatment
    assert str((tmp_path / "skills" / "reviewer").resolve()) in treatment


def test_planner_learning_ab_switch_targets_planner_bucket(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = SkillStore(tmp_path / "skills")
    mission = PlannerMission(store)
    common = dict(
        continuous_objective="improve the repository",
        journal_tail="",
        planning_cycle=0,
        mission=mission,
    )

    control = Planner._build_planner_prompt(
        **common,
        memory_maintenance_enabled=False,
    )
    treatment = Planner._build_planner_prompt(
        **common,
        memory_maintenance_enabled=True,
    )

    assert "Planner self-evolution" not in control
    assert "Planner self-evolution" in treatment
    assert str((tmp_path / "skills" / "planner").resolve()) in treatment


def test_manager_learning_ab_switch_targets_manager_bucket(tmp_path) -> None:
    store = SkillStore(tmp_path / "skills")

    control = Manager(
        project_root=tmp_path,
        skill_store=store,
        memory_maintenance_enabled=False,
    )._role_skill_block("delivery", include_libraries=False)
    treatment = Manager(
        project_root=tmp_path,
        skill_store=store,
        memory_maintenance_enabled=True,
    )._role_skill_block("delivery", include_libraries=False)

    assert "Manager self-evolution" not in control
    assert "Manager self-evolution" in treatment
    assert str((tmp_path / "skills" / "manager").resolve()) in treatment
