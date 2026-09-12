from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from argus_skill.core.models import ReviewDecision
from argus_skill.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus_skill.life.supervisor._planning_cycle_enqueue import _apply_planner_stage_request
from argus_skill.manager import Manager
from argus_skill.skills.stage_machine import (
    ChecklistItem,
    StageCompletionError,
    StageRollbackError,
    reset_stage_for_replacement_intent,
    rollback_stage,
)
from argus_skill.skills.vertical_select import persist_vertical
from argus_skill.verticals import _registry


def _external_project(tmp_path: Path, monkeypatch, policy: bool | None):
    """Register an external provider and use the normal persisted selection."""
    provider = ModuleType("external_lab.stages")
    provider.ARGUS_VERTICAL_API_VERSION = _registry.VERTICAL_API_VERSION
    provider.VERTICAL_PURPOSE = "Build and verify an external deliverable"
    provider.CHECKLIST_STAGE_ORDER = ("build", "verify", "deliver")
    provider.CHECKLIST_ITEMS = {
        stage: (ChecklistItem(f"{stage}.done", f"Verify {stage}", "evidence"),)
        for stage in provider.CHECKLIST_STAGE_ORDER
    }
    provider.completion_gate = "none"
    if policy is not None:
        provider.ALLOW_STAGE_ROLLBACK = policy
    entry = SimpleNamespace(name="external_lab", value=provider.__name__, load=lambda: provider)
    monkeypatch.setattr(_registry, "entry_points", lambda group: [entry])
    state = tmp_path / "state"
    workdir = tmp_path / "work"
    workdir.mkdir()
    persist_vertical(state, "external_lab", workflow_mode="staged")
    payload = read_pipeline_state(state)
    payload.update({
        "current_stage": "verify",
        "stages": {"build": {"status": "done"}, "verify": {"status": "in_progress"}},
    })
    write_pipeline_state(state, payload)
    return state, workdir, provider


@pytest.mark.parametrize("entrypoint", ["stage", "planner", "manager"])
@pytest.mark.parametrize("policy", [False, True, None], ids=["forward_only", "rollback", "default"])
def test_external_vertical_rollback_policy_across_runtime_entries(
    tmp_path: Path, monkeypatch, entrypoint: str, policy: bool | None,
) -> None:
    state, workdir, _provider = _external_project(tmp_path, monkeypatch, policy)
    before = read_pipeline_state(state)
    if entrypoint == "manager":
        prompts = []

        def model(prompt: str):
            prompts.append(prompt)
            return SimpleNamespace(last_agent_message=(
                "ACTION=rollback\nTARGET_STAGE=build\nREASON=Repair upstream evidence."
            ))

        transition = Manager(
            project_root=state, execution_workdir=workdir, runner=object(),
        ).decide_stage_transition(
            review=ReviewDecision(
                status="replan_requested", reason="Upstream evidence failed.",
                next_action="Return to build and repair the evidence.",
            ),
            project_root=state,
            mission_scope="bounded",
            stage_closing=True,
            run_exec=model,
        )
        assert len(prompts) == 1
        if policy is False:
            assert transition.action == "hold"
            assert "forward-only" in transition.reason
            assert transition.diagnostic == "external_lab_rollback_rejected"
            assert "Legal ROLLBACK targets: none" in prompts[0]
        else:
            assert transition.action == "rollback"
            assert "Legal ROLLBACK targets: none" not in prompts[0]
    else:
        def apply():
            if entrypoint == "stage":
                rollback_stage(state, target_stage="build", reason="Repair upstream evidence.")
            else:
                _apply_planner_stage_request(
                    state_root=state, requested_stage="build",
                    reason="Repair upstream evidence.", evidence_root=workdir,
                )

        if policy is False:
            with pytest.raises(StageRollbackError, match="external_lab stages are forward-only"):
                apply()
        else:
            apply()

    after = read_pipeline_state(state)
    if policy is False:
        assert after == before
    else:
        assert after["current_stage"] == "build"
        assert after["stages"]["verify"]["status"] == "pending"
        assert after["stage_history"][-1]["direction"] == "rollback"
        assert after["rollback_history"][-1]["from_stage"] == "verify"


def test_forward_only_planner_request_preserves_completion_failure(tmp_path, monkeypatch) -> None:
    state, workdir, provider = _external_project(tmp_path, monkeypatch, False)
    provider.stage_completion_issues = lambda *_args: ("verification evidence is missing",)
    before = read_pipeline_state(state)

    with pytest.raises(StageCompletionError, match="verification evidence is missing"):
        _apply_planner_stage_request(
            state_root=state, requested_stage="deliver", reason="Finish verification.",
            evidence_root=workdir,
        )

    assert read_pipeline_state(state) == before


def test_replacement_intent_can_reset_a_forward_only_vertical(tmp_path, monkeypatch) -> None:
    state, _workdir, _provider = _external_project(tmp_path, monkeypatch, False)

    reset_stage_for_replacement_intent(
        state, target_stage="build", reason="The operator replaced the objective.",
    )

    payload = read_pipeline_state(state)
    assert payload["current_stage"] == "build"
    assert payload["stage_history"][-1]["direction"] == "reset"
