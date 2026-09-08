from __future__ import annotations

from pathlib import Path

import pytest

from argus_skill.life.supervisor import _planning_cycle_enqueue as module
from argus_skill.skills.vertical_select import persist_vertical


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
    from argus_skill.verticals.research.stages import stage_completion_issues

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
    assert not module._research_stage_ready_for_close(
        state_root=state_root,
        evidence_root=workdir,
    )


def test_research_first_stage_ready_when_provider_gate_is_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_stage_auto_close_allowed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "argus_skill.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "research", "current_stage": "idea"},
    )
    definition = object()
    monkeypatch.setattr(
        "argus_skill.verticals._base.load_vertical",
        lambda *_args, **_kwargs: definition,
    )
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_checklist_stage_order",
        lambda _definition: ("idea", "build", "experiment", "paper", "review"),
    )
    gate_call: dict[str, object] = {}

    def completion_issues(*args, **kwargs):
        gate_call.update({"args": args, **kwargs})
        return ()

    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_stage_completion_issues",
        completion_issues,
    )
    state_root = tmp_path / "state"
    evidence_root = tmp_path / "workdir"

    assert module._research_stage_ready_for_close(
        state_root=state_root,
        evidence_root=evidence_root,
    )
    assert gate_call == {
        "args": (definition,),
        "stage": "idea",
        "project_root": evidence_root,
        "state_root": state_root,
    }


def test_required_portfolio_opts_in_but_still_needs_completed_evidence(
    tmp_path: Path, monkeypatch,
) -> None:
    from argus_skill.verticals.research import stages

    state_root, workdir = tmp_path / "state", tmp_path / "workdir"
    workdir.mkdir()
    persist_vertical(
        state_root, "research", workflow_mode="staged",
        research_target_level="publishable", research_direction_mode="broad",
    )
    assert stages.stage_auto_close_allowed("idea", workdir, state_root=state_root)
    assert not stages.stage_auto_close_allowed("build", workdir, state_root=state_root)
    assert not module._research_stage_ready_for_close(
        state_root=state_root, evidence_root=workdir,
    )

    monkeypatch.setattr(stages, "stage_completion_issues", lambda *_args, **_kwargs: ())
    assert module._research_stage_ready_for_close(
        state_root=state_root, evidence_root=workdir,
    )


def test_research_auto_close_derives_first_stage_not_old_literal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_stage_auto_close_allowed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "argus_skill.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "research", "current_stage": "research"},
    )
    monkeypatch.setattr(
        "argus_skill.verticals._base.load_vertical",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_checklist_stage_order",
        lambda _definition: ("idea", "build", "experiment", "paper", "review"),
    )

    assert not module._research_stage_ready_for_close(
        state_root=tmp_path / "state",
        evidence_root=tmp_path / "workdir",
    )


def test_research_first_stage_does_not_close_with_blockers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_stage_auto_close_allowed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "argus_skill.core.pipeline_state.read_pipeline_state",
        lambda _root: {"vertical": "research", "current_stage": "idea"},
    )
    monkeypatch.setattr(
        "argus_skill.verticals._base.load_vertical",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_checklist_stage_order",
        lambda _definition: ("idea", "build"),
    )
    monkeypatch.setattr(
        "argus_skill.verticals._base.vertical_stage_completion_issues",
        lambda *_args, **_kwargs: ("selection incomplete",),
    )

    assert not module._research_stage_ready_for_close(
        state_root=tmp_path / "state",
        evidence_root=tmp_path / "workdir",
    )
