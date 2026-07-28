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
        project_root=tmp_path,
    )
    planner = Planner._build_planner_prompt(
        continuous_objective="research the system",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
    )

    for prompt in (manager, engineer, planner):
        assert "direct read/write" in prompt
        assert str(wiki.resolve()) in prompt or "project: demo" in prompt
        assert "Skills store procedures" in prompt or "A Skill is procedural" in prompt
        assert "events and checkpoint.md" in prompt.lower()
