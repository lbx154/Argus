from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.core.vertical_contract import VerticalContract
from argus.life.supervisor import _planning_cycle_enqueue as module
from argus.skills.stage_machine import ChecklistItem
from argus.skills.vertical_select import persist_vertical


@pytest.mark.parametrize(
    ("workflow_mode", "target_level", "direction"),
    [
        ("staged", "publishable", "locked"),
        ("staged", "exploratory", "broad"),
        ("direct", "publishable", "broad"),
    ],
)
def test_no_portfolio_requirement_does_not_certify_unfinished_idea(
    tmp_path: Path,
    workflow_mode: str,
    target_level: str,
    direction: str,
) -> None:
    from argus.verticals.research.stages import stage_completion_issues

    state_root = tmp_path / "state"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(
        state_root,
        "research",
        workflow_mode=workflow_mode,
        research_target_level=target_level,
        research_direction_mode=direction,
    )
    # This empty gate caused a failed Idea mission's recovery plan to be
    # discarded. These paths still need a real Reviewer/Manager decision.
    assert stage_completion_issues("idea", workdir, state_root=state_root) == ()
    assert not module._automatic_stage_target(
        state_root=state_root,
        evidence_root=workdir,
    )


def test_research_first_stage_ready_when_provider_gate_is_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "research", "current_stage": "idea"},
    )
    gate_call: dict[str, object] = {}

    def automatic_completion(**kwargs):
        gate_call.update(kwargs)
        return True

    contract = VerticalContract(
        name="research", stage_order=("idea", "build", "experiment", "paper", "review"),
        checklist_items={}, completion_gate="none",
        automatic_stage_completion=automatic_completion,
    )
    monkeypatch.setattr(
        "argus.verticals._base.load_vertical_contract", lambda *args, **kwargs: contract,
    )
    state_root = tmp_path / "state"
    evidence_root = tmp_path / "workdir"

    assert module._automatic_stage_target(
        state_root=state_root,
        evidence_root=evidence_root,
    ) == "build"
    assert gate_call == {
        "stage": "idea",
        "project_root": evidence_root,
        "state_root": state_root,
    }


def test_required_portfolio_still_needs_completed_evidence(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus.verticals.research import stages

    state_root, workdir = tmp_path / "state", tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(
        state_root, "research", workflow_mode="staged",
        research_target_level="publishable", research_direction_mode="broad",
    )
    assert not stages.automatic_stage_completion_ready(
        stage="idea", project_root=workdir, state_root=state_root,
    )
    assert module._automatic_stage_target(state_root=state_root, evidence_root=workdir) == ""

    monkeypatch.setattr(stages, "stage_completion_issues", lambda *_args, **_kwargs: ())
    assert stages.automatic_stage_completion_ready(
        stage="idea", project_root=workdir, state_root=state_root,
    )
    assert module._automatic_stage_target(
        state_root=state_root, evidence_root=workdir,
    ) == stages.CHECKLIST_STAGE_ORDER[1]
    assert not stages.automatic_stage_completion_ready(
        stage="build", project_root=workdir, state_root=state_root,
    )


@pytest.mark.parametrize("current,target", [
    ("plan", "build"), ("build", "verify"), ("verify", ""), ("unknown", ""),
])
def test_automatic_stage_target_uses_active_vertical_and_next_stage(
    tmp_path: Path, monkeypatch, current: str, target: str,
) -> None:
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("plan", "build", "verify"),
        CHECKLIST_ITEMS={
            name: (ChecklistItem(f"{name}.done", "Complete stage", "Evidence exists"),)
            for name in ("plan", "build", "verify")
        },
        completion_gate="none", automatic_stage_completion_ready=lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "argus.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "custom_example", "current_stage": current},
    )
    loaded = []

    def load(name, *, project_root):
        loaded.append((name, project_root))
        return provider

    monkeypatch.setattr("argus.verticals._base.load_vertical", load)
    assert module._automatic_stage_target(
        state_root=tmp_path / "state", evidence_root=tmp_path / "evidence",
    ) == target
    assert loaded == [("custom_example", tmp_path / "state")]


def test_string_false_from_provider_never_advances_a_stage(tmp_path: Path, monkeypatch):
    provider = SimpleNamespace(
        CHECKLIST_STAGE_ORDER=("plan", "build"),
        CHECKLIST_ITEMS={
            name: (ChecklistItem(f"{name}.done", "Complete stage", "Evidence exists"),)
            for name in ("plan", "build")
        },
        completion_gate="none", automatic_stage_completion_ready=lambda **_kwargs: "false",
    )
    monkeypatch.setattr(
        "argus.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "custom_example", "current_stage": "plan"},
    )
    monkeypatch.setattr("argus.verticals._base.load_vertical", lambda *_args, **_kwargs: provider)
    assert module._automatic_stage_target(
        state_root=tmp_path / "state", evidence_root=tmp_path / "evidence",
    ) == ""


@pytest.mark.parametrize("current,ready", [("research", True), ("idea", False)])
def test_auto_close_requires_a_current_stage_and_provider_approval(
    tmp_path: Path, monkeypatch, current: str, ready: bool,
) -> None:
    monkeypatch.setattr(
        "argus.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "research", "current_stage": current},
    )
    contract = VerticalContract(
        name="research", stage_order=("idea", "build"),
        checklist_items={}, completion_gate="none",
        automatic_stage_completion=lambda **kwargs: ready,
    )
    monkeypatch.setattr(
        "argus.verticals._base.load_vertical_contract", lambda *args, **kwargs: contract,
    )
    assert not module._automatic_stage_target(
        state_root=tmp_path / "state", evidence_root=tmp_path / "workdir",
    )
