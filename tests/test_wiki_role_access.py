from __future__ import annotations

from pathlib import Path

from argus.planner.planner import Planner
from argus.skills.vertical_select import persist_vertical
from argus.wiki.bootstrap import init_wiki


def test_planner_uses_session_state_for_vertical_and_workspace_for_wiki(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wiki = init_wiki("demo", base=workspace)
    persist_vertical(state, "argus_maintenance")

    prompt = Planner._build_planner_prompt(
        continuous_objective="repair the current runtime objective",
        journal_tail="",
        planning_cycle=0,
        runtime_change_summary="",
        mission=None,
        project_root=workspace,
        state_root=state,
    )

    assert "current: `inspect`" in prompt
    assert "sequence: inspect, change, verify" in prompt
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
    assert 'context, not a hard boundary' in prompt


