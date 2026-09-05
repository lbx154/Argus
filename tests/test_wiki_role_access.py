from __future__ import annotations

from pathlib import Path

from argus_skill.planner.planner import Planner
from argus_skill.roles.prompts.engineer import build_mission_prompt
from argus_skill.roles.prompts.manager import build_simple_prompt
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.wiki.bootstrap import init_wiki


def test_manager_engineer_and_planner_share_direct_wiki_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wiki = init_wiki("demo", base=tmp_path)
    persist_vertical(tmp_path, "research")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(tmp_path))

    manager = build_simple_prompt(
        objective="explain the project",
        operator_workspace=str(tmp_path),
    )
    engineer = build_mission_prompt(
        task="implement the next increment",
        skill_text="",
        next_action=None,
        require_post_task_learning=True,
        project_root=tmp_path,
        project_skill_dir=tmp_path / "skills" / "engineer",
    )
    planner = Planner._build_planner_prompt(
        continuous_objective="research the system",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )

    for prompt in (manager, engineer, planner):
        assert "Wiki" in prompt
        assert str(wiki.resolve()) in prompt
        assert "INDEX.md" in prompt
        assert "sources/" not in prompt
        assert "query_pack.md" not in prompt

    assert "Check primary sources only when an external technical claim matters" in manager
    assert "Use primary sources when external behavior matters" in engineer
    assert "If repeated attempts fail" in engineer
    assert "recheck the underlying assumption" in engineer
    assert "When durable declarative knowledge changes" in engineer
    assert "support/limitation matrices" in engineer
    assert "Procedures and checklists belong in Skills" in engineer
    assert "route durable project facts" in engineer
    assert "external algorithm" in planner
    assert "starting context, not a" in planner
    assert "fresh paper/source/issue/hardware investigation" in planner


def test_planner_uses_session_state_for_vertical_and_workspace_for_wiki(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wiki = init_wiki("demo", base=workspace)
    persist_vertical(state, "math")

    prompt = Planner._build_planner_prompt(
        continuous_objective="solve the current mathematical objective",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
        project_root=workspace,
        state_root=state,
    )

    assert "current: `scope`" in prompt
    assert "sequence: scope, solve, review" in prompt
    assert str(wiki.resolve()) in prompt
    assert "Pipeline stage order for this vertical: research, plan" not in prompt


def test_direct_workflow_planner_has_no_stage_gate(tmp_path: Path) -> None:
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persist_vertical(state, "software", workflow_mode="direct")

    prompt = Planner._build_planner_prompt(
        continuous_objective="finish the next project milestone",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
        project_root=workspace,
        state_root=state,
    )

    assert "## Stage gate" not in prompt
    assert "## Stage checklist" not in prompt
    assert "Downstream stages (LOCKED" not in prompt
    assert "## Current workflow stage" in prompt
    assert "semantic context, not a hard boundary" in prompt
