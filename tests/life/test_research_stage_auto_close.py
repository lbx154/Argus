from __future__ import annotations

from pathlib import Path

from argus_skill.life.supervisor import _planning_cycle_enqueue as module


def test_research_first_stage_ready_when_provider_gate_is_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
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


def test_research_auto_close_derives_first_stage_not_old_literal(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
