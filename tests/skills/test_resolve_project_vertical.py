"""A project's vertical is read from its pipeline state, else from the Manager's record.

The trial host's control project ran nine research missions without ever
writing PIPELINE_STATE.json into its workspace; the vertical lived only in
the project's manager-handoff.json. Everything that files knowledge by
vertical must find it there too.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.pipeline_state import write_pipeline_state
from argus.skills.vertical_select import resolve_project_vertical


def test_the_manager_record_answers_when_the_workspace_has_no_state(tmp_path: Path) -> None:
    workspace, life = tmp_path / "ws", tmp_path / "life"
    workspace.mkdir()
    life.mkdir()
    assert resolve_project_vertical(workspace, life_dir=life) == ""
    (life / "manager-handoff.json").write_text(json.dumps({"version": 3, "vertical": "research", "domain": ""}))
    assert resolve_project_vertical(workspace, life_dir=life) == "research"
    assert resolve_project_vertical(workspace) == ""


def test_the_mission_view_is_the_second_record_and_unknown_names_are_ignored(tmp_path: Path) -> None:
    workspace, life = tmp_path / "ws", tmp_path / "life"
    workspace.mkdir()
    life.mkdir()
    (life / "manager-handoff.json").write_text(json.dumps({"vertical": "not-a-vertical"}))
    (life / "mission-view.json").write_text(json.dumps({"routing": {"vertical": "software"}}))
    assert resolve_project_vertical(workspace, life_dir=life) == "software"
    (life / "mission-view.json").write_text("{not json")
    assert resolve_project_vertical(workspace, life_dir=life) == ""


def test_the_workspace_state_wins_over_the_record(tmp_path: Path) -> None:
    workspace, life = tmp_path / "ws", tmp_path / "life"
    workspace.mkdir()
    life.mkdir()
    write_pipeline_state(workspace, {"vertical": "software"})
    (life / "manager-handoff.json").write_text(json.dumps({"vertical": "research"}))
    assert resolve_project_vertical(workspace, life_dir=life) == "software"
