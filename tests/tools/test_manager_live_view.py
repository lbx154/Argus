from __future__ import annotations

import json

from argus_skill.manager.live_view import (
    load_live_view_decision,
    manager_workspace_capability_prompt,
)
from argus_skill.tools.manager_live_view import clear_view, set_view


def test_manager_tool_sets_workspace_artifact_and_emits_event(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    proof = workspace / "research" / "proof.pdf"
    proof.parent.mkdir(parents=True)
    proof.write_bytes(b"%PDF-1.4\n")
    state.mkdir()

    result = set_view(
        workspace=workspace,
        state_dir=state,
        title="Current proof",
        reason="Manager selected the reviewed PDF.",
        paths=["research/proof.pdf"],
    )

    assert result["ok"] is True
    assert result["view"]["paths"][0]["exists"] is True
    view = load_live_view_decision(workspace, manifest_root=state)
    assert view is not None and view.paths == ("research/proof.pdf",)
    event = json.loads((state / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert event["type"] == "manager.live_view.updated"
    assert event["source"] == "manager_tool"


def test_manager_tool_rejects_state_only_or_missing_artifact(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    (state / "manager_live").mkdir(parents=True)
    (state / "manager_live" / "proof.pdf").write_bytes(b"%PDF")

    result = set_view(
        workspace=workspace,
        state_dir=state,
        title="Broken",
        reason="Wrong root.",
        paths=["manager_live/proof.pdf"],
    )

    assert result["ok"] is False
    assert result["missing"] == ["manager_live/proof.pdf"]


def test_manager_tool_clear_and_prompt_expose_distinct_roots(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    prompt = manager_workspace_capability_prompt(workspace, manifest_root=state)

    assert str(workspace.resolve()) in prompt
    assert str(state.resolve()) in prompt
    assert "canonical workspace" in prompt
    assert "manager_live_view" in prompt
    assert clear_view(workspace=workspace, state_dir=state)["view"] is None
