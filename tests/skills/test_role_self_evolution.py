from __future__ import annotations

from types import SimpleNamespace

from argus.manager import Manager
from argus.planner import Planner
from argus.reviewer import Reviewer
from argus.roles.prompts.engineer import build_mission_prompt
from argus.skills.layered import LayeredSkillStore
from argus.skills.missions import PlannerMission, SelfMission
from argus.skills.role_memory import (
    profile_role_skill_dir,
    profile_self_skill_dir,
    project_role_skill_dir,
)
from argus.skills.store import SkillStore
from tests.role_rules import lists, rule


def test_global_role_self_evolution_ab_knob(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "0")
    assert Planner(None, memory_maintenance_enabled=None).memory_maintenance_enabled is False

    monkeypatch.setenv("ARGUS_SKILL_LEARNING", "1")
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


def test_engineer_learning_stays_inside_the_workspace(tmp_path) -> None:
    prompt = build_mission_prompt(
        task="repair the parser",
        skill_text="",
        next_action=None,
        require_post_task_learning=True,
        project_root=tmp_path,
    )

    skill_dir = tmp_path / ".agents" / "skills"
    assert f"Project Skill directory: {skill_dir}" in prompt
    assert lists(prompt, "durable-learning/SKILL.md")
    assert "Each Skill is `<name>/SKILL.md`" in rule("engineer", "durable-learning/SKILL.md")
    assert "keep the script you ran beside it" in rule("engineer", "durable-learning/SKILL.md")
    assert "Keep only reusable role learning" in rule("engineer", "durable-learning/SKILL.md")
    assert "Deliver the requested outputs, not substitutes" in rule("engineer", "doing-the-task/SKILL.md")
    learning = rule("engineer", "durable-learning/SKILL.md")
    assert "Durable project facts, contracts, support limits" in learning
    assert "never write shared or global layers" in learning


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
    assert 'change no source, output or build file' in treatment


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

    assert lists(prompt, "checking-the-work/SKILL.md")
    assert "Identity drift (a changed checksum, digest or commit) proves neither failure nor causation" in rule("reviewer", "checking-the-work/SKILL.md")


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

