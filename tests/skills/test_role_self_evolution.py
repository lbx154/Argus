from __future__ import annotations

from types import SimpleNamespace

from argus_skill.manager import Manager
from argus_skill.planner import Planner
from argus_skill.reviewer import Reviewer
from argus_skill.roles.prompts.engineer import build_mission_prompt
from argus_skill.skills.layered import LayeredSkillStore
from argus_skill.skills.missions import PlannerMission, SelfMission
from argus_skill.skills.role_memory import (
    profile_role_skill_dir,
    profile_self_skill_dir,
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


def test_profile_role_skill_directories_are_cross_session(tmp_path) -> None:
    store = SimpleNamespace(global_=SimpleNamespace(skills_dir=tmp_path / "profile"))

    for role in ("self", "manager", "planner", "engineer", "reviewer"):
        assert profile_role_skill_dir(store, role) == (
            tmp_path / "profile" / role
        ).resolve()
    assert profile_self_skill_dir(store) == (tmp_path / "profile" / "self").resolve()


def test_new_session_discovers_profile_self_skill(tmp_path) -> None:
    profile = tmp_path / "profile"
    self_dir = profile / "self"
    self_dir.mkdir(parents=True)
    learned = self_dir / "operator-vocabulary.md"
    learned.write_text(
        "---\nname: operator vocabulary\ndescription: Interpret recurring terms\n---\n",
        encoding="utf-8",
    )
    first = LayeredSkillStore(
        project_dir=tmp_path / "session-a",
        global_dir=profile,
    )
    second = LayeredSkillStore(
        project_dir=tmp_path / "session-b",
        global_dir=profile,
    )

    assert self_dir.resolve() in SelfMission(first).libraries().native_paths
    assert self_dir.resolve() in SelfMission(second).libraries().native_paths
    manager = Manager(
        project_root=tmp_path,
        runner=SimpleNamespace(),
        skill_store=second,
    )
    assert manager._session is not None
    assert str(self_dir.resolve()) in manager._session.skill_paths


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
    assert "No unrelated cleanup/hardening" in prompt
    assert "Keep only reusable role learning here" in prompt
    assert "route durable project facts" in prompt
    assert "never write shared/global layers" in prompt


def test_main_reviewer_never_edits_skills_directly(tmp_path) -> None:
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
    assert "Reviewer self-evolution" not in treatment
    assert str((tmp_path / "skills" / "reviewer").resolve()) not in treatment
    assert "You do not change the work under review" in treatment


def test_reviewer_protected_resource_evidence_requires_a_traceable_mutation(
    tmp_path,
) -> None:
    prompt = Reviewer(
        runner=None,
        skill_store=SkillStore(tmp_path / "skills"),
        memory_maintenance_enabled=False,
    )._build_prompt(
        objective="Verify deployment without operating the protected service.",
        operator_messages=[],
        planner_review_instruction="",
        round_index=1,
        session_id=None,
        main_summary="The protected service identity changed between observations.",
        main_error=None,
        working_dir=tmp_path,
    )

    assert "External identity drift without a mission mutation" in prompt
    assert "mutation command attributable to this mission" in prompt


def test_planner_does_not_write_skills_during_planning(tmp_path, monkeypatch) -> None:
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
    assert "Planner self-evolution" not in treatment
    assert str((tmp_path / "skills" / "planner").resolve()) not in treatment


def test_the_planner_may_keep_judgement_where_the_engineer_keeps_procedure(
    tmp_path,
) -> None:
    """A role can only retain the shape of learning its decisions produce.

    Planner lessons are campaign-level judgement, not shell steps. Asking every
    role for a "reusable procedure" filed the strategic lesson under generic
    advice and dropped it — the learning that would question a stalled plan
    soonest was the learning the contract refused to store.
    """
    store = SkillStore(tmp_path / "skills")

    planner = role_skill_maintenance_block(store, "planner", enabled=True)
    assert "strategic decision heuristic" in planner
    assert "where it applies" in planner
    assert "one case where following it would be wrong" in planner

    engineer = role_skill_maintenance_block(store, "engineer", enabled=True)
    assert "a reusable procedure" in engineer
    assert "strategic decision heuristic" not in engineer


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
