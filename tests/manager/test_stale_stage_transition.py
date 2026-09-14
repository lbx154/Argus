from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.core.models import ReviewDecision
from argus_skill.core.pipeline_state import pipeline_state_path, read_pipeline_state
from argus_skill.manager import Manager
from argus_skill.manager.control_state import CampaignControlStore
from argus_skill.skills.stage_machine import (
    advance_stage,
    reset_stage_for_replacement_intent,
)
from argus_skill.skills.vertical_select import persist_vertical


def _manager(tmp_path: Path, *, workflow_mode: str = "staged"):
    state = tmp_path / "state"
    work = tmp_path / "work"
    work.mkdir()
    persist_vertical(state, "math_synth", workflow_mode=workflow_mode)
    return Manager(project_root=state, execution_workdir=work, runner=object()), state, work


def _review() -> ReviewDecision:
    return ReviewDecision(status="done", reason="Old task verified.", next_action="")


def test_replacement_can_commit_during_model_call_and_rejects_old_advance(tmp_path) -> None:
    manager, state, work = _manager(tmp_path)
    advance_stage(state, target_stage="optimize", reason="setup done", evidence_root=work)
    replacement: list[bytes] = []
    replaced = threading.Event()

    def replace_intent() -> None:
        with manager.pipeline_lock():
            reset_stage_for_replacement_intent(
                state, target_stage="setup", reason="new operator objective", evidence_root=work
            )
            replacement.append(pipeline_state_path(state).read_bytes())
        replaced.set()

    def model(_prompt):
        # A replacement must not wait for the slow model call to return.
        worker = threading.Thread(target=replace_intent, daemon=True)
        worker.start()
        assert replaced.wait(timeout=2), "stage decision held the pipeline lock during inference"
        worker.join(timeout=1)
        return SimpleNamespace(last_agent_message="ACTION=advance\nTARGET_STAGE=measure\nREASON=old optimization done")

    decision = manager.decide_stage_transition(
        review=_review(), project_root=state, run_exec=model
    )

    assert replaced.is_set()
    assert decision.action == "hold"
    assert decision.source == "stale_stage_context_hold"
    assert decision.current_stage == decision.target_stage == "setup"
    assert pipeline_state_path(state).read_bytes() == replacement[0]
    assert read_pipeline_state(state)["stages"]["optimize"].get("status") not in {"done", "skipped"}


@pytest.mark.parametrize("replacement_kind", ["same_stage_reset", "generation", "objective", "control_revision"])
def test_same_stage_replacement_cannot_be_completed_by_old_review(tmp_path, replacement_kind) -> None:
    manager, state, work = _manager(tmp_path, workflow_mode="direct")
    continuous = state / "continuous.json"
    continuous.write_text(json.dumps({"objective": "old objective", "generation": 1}))
    control = CampaignControlStore(state)
    control.commit_revision(identity=control.campaign_identity(), updates={}, reason="initial")
    replacement: list[bytes] = []

    def model(_prompt):
        with manager.pipeline_lock():
            if replacement_kind == "same_stage_reset":
                reset_stage_for_replacement_intent(
                    state, target_stage="setup", reason="replacement", evidence_root=work
                )
            elif replacement_kind == "generation":
                continuous.write_text(json.dumps({"objective": "old objective", "generation": 2}))
            elif replacement_kind == "objective":
                continuous.write_text(json.dumps({"objective": "replacement objective", "generation": 1}))
            else:
                control.commit_revision(
                    identity=control.campaign_identity(),
                    updates={"operator_boundary": "changed"},
                    reason="new operator authority",
                )
            replacement.append(pipeline_state_path(state).read_bytes())
        return SimpleNamespace(last_agent_message="ACTION=complete\nTARGET_STAGE=setup\nREASON=old objective done")

    decision = manager.decide_stage_transition(
        review=_review(), project_root=state, mission_scope="bounded", run_exec=model
    )

    assert decision.action == "hold"
    assert decision.diagnostic == "stage_context_changed"
    assert decision.current_stage == decision.target_stage == "setup"
    assert pipeline_state_path(state).read_bytes() == replacement[0]
    assert read_pipeline_state(state).get("stages", {}).get("setup", {}).get("status") != "done"


def test_idempotent_vertical_refresh_does_not_invalidate_stage_decision(tmp_path) -> None:
    manager, state, _work = _manager(tmp_path, workflow_mode="direct")

    def model(_prompt):
        with manager.pipeline_lock():
            persist_vertical(state, "math_synth", workflow_mode="direct")
        return SimpleNamespace(last_agent_message="ACTION=complete\nTARGET_STAGE=setup\nREASON=objective done")

    decision = manager.decide_stage_transition(
        review=_review(), project_root=state, mission_scope="bounded", run_exec=model
    )

    assert decision.action == "complete"
    assert read_pipeline_state(state)["stages"]["setup"]["status"] == "done"


def test_deterministic_preflight_cannot_commit_over_replacement(tmp_path, monkeypatch) -> None:
    from argus_skill.skills import stage_machine

    manager, state, work = _manager(tmp_path)
    original = stage_machine._ensure_stage_completion
    replacement: list[bytes] = []

    def preflight(*args, **kwargs):
        with manager.pipeline_lock():
            reset_stage_for_replacement_intent(
                state, target_stage="setup", reason="replacement during preflight", evidence_root=work
            )
            replacement.append(pipeline_state_path(state).read_bytes())
        return original(*args, **kwargs)

    monkeypatch.setattr(stage_machine, "_ensure_stage_completion", preflight)
    decision = manager.decide_stage_transition(
        review=_review(), project_root=state, stage_closing=True,
        run_exec=lambda _prompt: pytest.fail("stale deterministic verdict must not call a model"),
    )

    assert decision.action == "hold"
    assert decision.source == "stale_stage_context_hold"
    assert pipeline_state_path(state).read_bytes() == replacement[0]
